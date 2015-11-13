# This file is part of Indico.
# Copyright (C) 2002 - 2015 European Organization for Nuclear Research (CERN).
#
# Indico is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# Indico is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Indico; if not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import mimetypes
from collections import Counter
from copy import deepcopy
from datetime import datetime, date
from operator import itemgetter
from uuid import uuid4

import wtforms
from sqlalchemy.dialects.postgresql import ARRAY
from werkzeug.datastructures import FileStorage
from wtforms.validators import NumberRange, ValidationError, InputRequired

from indico.core.db import db
from indico.modules.events.registration.fields.base import (RegistrationFormFieldBase, RegistrationFormBillableField,
                                                            RegistrationFormBillableItemsField)
from indico.modules.events.registration.models.registrations import RegistrationData
from indico.util.date_time import format_date, iterdays, strftime_all_years
from indico.util.fs import secure_filename
from indico.util.i18n import _, L_
from indico.util.string import normalize_phone_number, snakify_keys
from indico.web.forms.fields import IndicoRadioField, JSONField
from indico.web.forms.validators import IndicoEmail
from MaKaC.webinterface.common.countries import CountryHolder


def get_field_merged_options(field, registration_data):
    rdata = registration_data.get(field.id)
    result = deepcopy(field.view_data)
    result['deletedChoice'] = []
    if not rdata or not rdata.data:
        return result
    values = [rdata.data['choice']] if 'choice' in rdata.data else rdata.data.keys()
    for val in values:
        if val and not any(item['id'] == val for item in result['choices']):
            field_data = rdata.field_data
            merged_data = field.field_impl.unprocess_field_data(field_data.versioned_data,
                                                                field_data.field.data)
            missing_option = next((choice for choice in merged_data['choices'] if choice['id'] == val), None)
            if missing_option:
                result['choices'].append(missing_option)
                result['deletedChoice'].append(missing_option['id'])
    return result


class TextField(RegistrationFormFieldBase):
    name = 'text'
    wtf_field_class = wtforms.StringField


class NumberField(RegistrationFormBillableField):
    name = 'number'
    wtf_field_class = wtforms.IntegerField

    @property
    def validators(self):
        min_value = self.form_item.data.get('min_value', None)
        return [NumberRange(min=min_value)] if min_value else None

    def calculate_price(self, reg_data, versioned_data):
        if not versioned_data.get('is_billable'):
            return 0
        return versioned_data.get('price', 0) * int(reg_data or 0)

    def get_friendly_data(self, registration_data):
        if registration_data.data is None:
            return ''
        return registration_data.data


class TextAreaField(RegistrationFormFieldBase):
    name = 'textarea'
    wtf_field_class = wtforms.StringField


class ChoiceBaseField(RegistrationFormBillableItemsField):
    versioned_data_fields = RegistrationFormBillableItemsField.versioned_data_fields | {'choices'}
    has_default_item = False
    wtf_field_class = JSONField

    @classmethod
    def unprocess_field_data(cls, versioned_data, unversioned_data):
        items = deepcopy(versioned_data['choices'])
        for item in items:
            item['caption'] = unversioned_data['captions'][item['id']]
        return {'choices': items}

    @property
    def filter_choices(self):
        return self.form_item.data['captions']

    @property
    def view_data(self):
        return dict(super(ChoiceBaseField, self).view_data, places_used=self.get_places_used())

    @property
    def validators(self):
        def _check_number_of_places(form, field):
            if not field.data:
                return
            if form.modified_registration:
                old_data = form.modified_registration.data_by_field.get(self.form_item.id)
                if not old_data or not self.has_data_changed(field.data, old_data):
                    return
            choices = self.form_item.versioned_data['choices']
            captions = self.form_item.data['captions']
            for k in field.data:
                choice = next((x for x in choices if x['id'] == k), None)
                places_limit = choice.get('places_limit')
                places_used_dict = self.get_places_used()
                places_used_dict.update(field.data)
                if places_limit and not (places_limit - places_used_dict.get(k, 0)) >= 0:
                    raise ValidationError(_('No places left for the option: {0}').format(captions[k]))
        return [_check_number_of_places]

    @classmethod
    def process_field_data(cls, data, old_data=None, old_versioned_data=None):
        unversioned_data, versioned_data = super(ChoiceBaseField, cls).process_field_data(data, old_data,
                                                                                          old_versioned_data)
        items = [x for x in versioned_data['choices'] if not x.get('remove')]
        captions = dict(old_data['captions']) if old_data is not None else {}
        if cls.has_default_item:
            unversioned_data.setdefault('default_item', None)
        for item in items:
            if 'id' not in item:
                item['id'] = unicode(uuid4())
            item.setdefault('is_billable', False)
            item['price'] = float(item['price']) if item.get('price') else 0
            item['places_limit'] = int(item['places_limit']) if item.get('places_limit') else 0
            item['max_extra_slots'] = int(item['max_extra_slots']) if item.get('max_extra_slots') else 0
            if cls.has_default_item and unversioned_data['default_item'] in {item['caption'], item['id']}:
                unversioned_data['default_item'] = item['id']
            captions[item['id']] = item.pop('caption')
        versioned_data['choices'] = items
        unversioned_data['captions'] = captions
        return unversioned_data, versioned_data

    def get_places_used(self):
        places_used = Counter()
        for registration in self.form_item.registration_form.active_registrations:
            if self.form_item.id not in registration.data_by_field:
                continue
            data = registration.data_by_field[self.form_item.id].data
            if not data:
                continue
            places_used.update(data)
        return dict(places_used)

    def create_sql_filter(self, data_list):
        return RegistrationData.data.has_any(db.func.cast(data_list, ARRAY(db.String)))

    def calculate_price(self, reg_data, versioned_data):
        if not reg_data:
            return 0
        billable_choices = [x for x in versioned_data['choices'] if x['id'] in reg_data and x['is_billable']]
        price = 0
        for billable_field in billable_choices:
            price += billable_field['price']
            if billable_field.get('extra_slots_pay'):
                price += (reg_data[billable_field['id']] - 1) * billable_field['price']
        return price


class SingleChoiceField(ChoiceBaseField):
    name = 'single_choice'
    has_default_item = True

    @property
    def default_value(self):
        data = self.form_item.data
        versioned_data = self.form_item.versioned_data
        try:
            default_item = data['default_item']
        except KeyError:
            return None
        # only use the default item if it exists in the current version
        return {default_item: 1} if any(x['id'] == default_item for x in versioned_data['choices']) else {}

    def get_friendly_data(self, registration_data):
        if not registration_data.data:
            return ''
        uuid, number_of_slots = registration_data.data.items()[0]
        caption = registration_data.field_data.field.data['captions'][uuid]
        return '{} (+{})'.format(caption, number_of_slots - 1) if number_of_slots > 1 else caption

    def process_form_data(self, registration, value, old_data=None, billable_items_locked=False):
        if billable_items_locked and old_data.price:
            # if the old field was paid we can simply ignore any change and keep the old value
            return {}
        # always store no-option as empty dict
        if value is None:
            value = {}
        return super(SingleChoiceField, self).process_form_data(registration, value, old_data, billable_items_locked)


class CheckboxField(RegistrationFormBillableField):
    name = 'checkbox'
    wtf_field_class = wtforms.BooleanField
    friendly_data_mapping = {None: '',
                             True: L_('Yes'),
                             False: L_('No')}

    def calculate_price(self, reg_data, versioned_data):
        if not versioned_data.get('is_billable') or not reg_data:
            return 0
        return versioned_data.get('price', 0)

    def get_friendly_data(self, registration_data):
        return self.friendly_data_mapping[registration_data.data]

    def get_places_used(self):
        places_used = 0
        if self.form_item.data.get('places_limit'):
            for registration in self.form_item.registration_form.active_registrations:
                if self.form_item.id not in registration.data_by_field:
                    continue
                if registration.data_by_field[self.form_item.id].data:
                    places_used += 1
        return places_used

    @property
    def view_data(self):
        return dict(super(CheckboxField, self).view_data, places_used=self.get_places_used())

    @property
    def filter_choices(self):
        return {unicode(val).lower(): caption for val, caption in self.friendly_data_mapping.iteritems()
                if val is not None}

    @property
    def validators(self):
        def _check_number_of_places(form, field):
            if form.modified_registration:
                old_data = form.modified_registration.data_by_field.get(self.form_item.id)
                if not old_data or not self.has_data_changed(field.data, old_data):
                    return
            if field.data and self.form_item.data.get('places_limit'):
                places_left = self.form_item.data.get('places_limit') - self.get_places_used()
                if not places_left:
                    raise ValidationError(_('There are no places left for this option.'))
        return [_check_number_of_places]


class DateField(RegistrationFormFieldBase):
    name = 'date'
    wtf_field_class = wtforms.StringField

    def process_form_data(self, registration, value, old_data=None, billable_items_locked=False):
        if value:
            date_format = self.form_item.data['date_format']
            value = datetime.strptime(value, date_format).isoformat()
        return super(DateField, self).process_form_data(registration, value, old_data, billable_items_locked)

    def get_friendly_data(self, registration_data):
        date_string = registration_data.data
        if not date_string:
            return ''
        dt = datetime.strptime(date_string, '%Y-%m-%dT%H:%M:%S')
        return strftime_all_years(dt, self.form_item.data['date_format'])

    @property
    def view_data(self):
        has_time = ' ' in self.form_item.data['date_format']
        return dict(super(DateField, self).view_data, has_time=has_time)


class BooleanField(RegistrationFormBillableField):
    name = 'bool'
    wtf_field_class = IndicoRadioField
    required_validator = InputRequired
    friendly_data_mapping = {None: '',
                             True: L_('Yes'),
                             False: L_('No')}

    @property
    def wtf_field_kwargs(self):
        return {'choices': [(True, _('Yes')), (False, _('No'))],
                'coerce': lambda x: {'yes': True, 'no': False}.get(x, None)}

    @property
    def filter_choices(self):
        return {unicode(val).lower(): caption for val, caption in self.friendly_data_mapping.iteritems()
                if val is not None}

    @property
    def view_data(self):
        return dict(super(BooleanField, self).view_data, places_used=self.get_places_used())

    @property
    def validators(self):
        def _check_number_of_places(form, field):
            if form.modified_registration:
                old_data = form.modified_registration.data_by_field.get(self.form_item.id)
                if not old_data or not self.has_data_changed(field.data, old_data):
                    return
            if field.data and self.form_item.data.get('places_limit'):
                places_left = self.form_item.data.get('places_limit') - self.get_places_used()
                if field.data and not places_left:
                    raise ValidationError(_('There are no places left for this option.'))
        return [_check_number_of_places]

    def get_places_used(self):
        places_used = 0
        if self.form_item.data.get('places_limit'):
            for registration in self.form_item.registration_form.active_registrations:
                if self.form_item.id not in registration.data_by_field:
                    continue
                if registration.data_by_field[self.form_item.id].data:
                    places_used += 1
        return places_used

    def calculate_price(self, reg_data, versioned_data):
        if not versioned_data.get('is_billable'):
            return 0
        return versioned_data.get('price', 0) if reg_data else 0

    def get_friendly_data(self, registration_data):
        return self.friendly_data_mapping[registration_data.data]


class PhoneField(RegistrationFormFieldBase):
    name = 'phone'
    wtf_field_class = wtforms.StringField
    wtf_field_kwargs = {'filters': [lambda x: normalize_phone_number(x) if x else '']}


class CountryField(RegistrationFormFieldBase):
    name = 'country'
    wtf_field_class = wtforms.SelectField

    @property
    def wtf_field_kwargs(self):
        return {'choices': CountryHolder.getCountries().items()}

    @classmethod
    def unprocess_field_data(cls, versioned_data, unversioned_data):
        choices = sorted([{'caption': v, 'countryKey': k} for k, v in CountryHolder.getCountries().iteritems()],
                         key=itemgetter('caption'))
        return {'choices': choices}

    @property
    def filter_choices(self):
        return dict(self.wtf_field_kwargs['choices'])

    def get_friendly_data(self, registration_data):
        return CountryHolder.getCountries()[registration_data.data] if registration_data.data else ''


class _DeletableFileField(wtforms.FileField):
    def process_formdata(self, valuelist):
        if not valuelist:
            self.data = {'keep_existing': False, 'uploaded_file': None}
        else:
            # This expects a form with a hidden field and a file field with the same name.
            # If the hidden field is empty, it indicates that an existing file should be
            # deleted or replaced with the newly uploaded file.
            keep_existing = '' not in valuelist
            uploaded_file = next((x for x in valuelist if isinstance(x, FileStorage)), None)
            if not uploaded_file or not uploaded_file.filename:
                uploaded_file = None
            self.data = {'keep_existing': keep_existing, 'uploaded_file': uploaded_file}


class FileField(RegistrationFormFieldBase):
    name = 'file'
    wtf_field_class = _DeletableFileField

    def process_form_data(self, registration, value, old_data=None, billable_items_locked=False):
        data = {'field_data': self.form_item.current_data}
        file_ = value['uploaded_file']
        if file_:
            # we have a file -> always save it
            data['file'] = {
                'data': file_.file,
                'name': secure_filename(file_.filename, 'attachment'),
                'content_type': mimetypes.guess_type(file_.filename)[0] or file_.mimetype or 'application/octet-stream'
            }
        elif not value['keep_existing']:
            data['file'] = None
        return data

    @property
    def default_value(self):
        return None

    def get_friendly_data(self, registration_data):
        if not registration_data:
            return ''
        return registration_data.filename


class EmailField(RegistrationFormFieldBase):
    name = 'email'
    wtf_field_class = wtforms.StringField
    wtf_field_kwargs = {'filters': [lambda x: x.lower() if x else x]}

    @property
    def validators(self):
        return [IndicoEmail()]


class AccommodationField(RegistrationFormBillableItemsField):
    name = 'accommodation'
    wtf_field_class = JSONField
    versioned_data_fields = RegistrationFormBillableField.versioned_data_fields | {'choices'}

    @classmethod
    def process_field_data(cls, data, old_data=None, old_versioned_data=None):
        unversioned_data, versioned_data = super(AccommodationField, cls).process_field_data(data, old_data,
                                                                                             old_versioned_data)
        items = [x for x in versioned_data['choices'] if not x.get('remove')]
        captions = dict(old_data['captions']) if old_data is not None else {}
        for item in items:
            if 'id' not in item:
                item['id'] = unicode(uuid4())
            item.setdefault('is_billable', False)
            item['price'] = float(item['price']) if item.get('price') else 0
            item['places_limit'] = int(item['places_limit']) if item.get('places_limit') else 0
            captions[item['id']] = item.pop('caption')
        for key in {'arrival_date_from', 'arrival_date_to', 'departure_date_from', 'departure_date_to'}:
            unversioned_data[key] = _to_machine_date(unversioned_data[key])
        versioned_data['choices'] = items
        unversioned_data['captions'] = captions
        return unversioned_data, versioned_data

    @classmethod
    def unprocess_field_data(cls, versioned_data, unversioned_data):
        data = {}
        arrival_date_from = _to_date(unversioned_data['arrival_date_from'])
        arrival_date_to = _to_date(unversioned_data['arrival_date_to'])
        departure_date_from = _to_date(unversioned_data['departure_date_from'])
        departure_date_to = _to_date(unversioned_data['departure_date_to'])
        data['arrival_dates'] = [(dt.date().isoformat(), format_date(dt))
                                 for dt in iterdays(arrival_date_from, arrival_date_to)]
        data['departure_dates'] = [(dt.date().isoformat(), format_date(dt))
                                   for dt in iterdays(departure_date_from, departure_date_to)]
        items = deepcopy(versioned_data['choices'])
        for item in items:
            item['caption'] = unversioned_data['captions'][item['id']]
        data['choices'] = items
        return data

    @property
    def validators(self):
        def _stay_dates_valid(form, field):
            if not field.data:
                return
            data = snakify_keys(field.data)
            try:
                arrival_date = data['arrival_date']
                departure_date = data['departure_date']
            except KeyError:
                raise ValidationError(_("Arrival/departure date is missing"))
            if _to_date(arrival_date) > _to_date(departure_date):
                raise ValidationError(_("Arrival date can't be set after the departure date."))

        def _check_number_of_places(form, field):
            if not field.data:
                return
            if form.modified_registration:
                old_data = form.modified_registration.data_by_field.get(self.form_item.id)
                if not old_data or not self.has_data_changed(snakify_keys(field.data), old_data):
                    return
            item = next((x for x in self.form_item.versioned_data['choices'] if x['id'] == field.data['choice']),
                        None)
            captions = self.form_item.data['captions']
            places_used_dict = self.get_places_used()
            if item and item['places_limit'] and not ((item['places_limit']
                                                       - places_used_dict.get(field.data['choice'], 0))):
                raise ValidationError(_('Not enough rooms in the {0}').format(captions[item['id']]))
        return [_stay_dates_valid, _check_number_of_places]

    @property
    def view_data(self):
        return dict(super(AccommodationField, self).view_data, places_used=self.get_places_used())

    def get_friendly_data(self, registration_data):
        friendly_data = dict(registration_data.data)
        if not friendly_data:
            return {}
        unversioned_data = registration_data.field_data.field.data
        friendly_data['choice'] = unversioned_data['captions'][friendly_data['choice']]
        friendly_data['arrival_date'] = _to_date(friendly_data['arrival_date'])
        friendly_data['departure_date'] = _to_date(friendly_data['departure_date'])
        friendly_data['nights'] = (friendly_data['departure_date'] - friendly_data['arrival_date']).days
        return friendly_data

    def calculate_price(self, reg_data, versioned_data):
        if not reg_data:
            return 0
        item = next((x for x in versioned_data['choices']
                     if reg_data['choice'] == x['id'] and x.get('is_billable', False)), None)
        if not item or not item['price']:
            return 0
        nights = (_to_date(reg_data['departure_date']) - _to_date(reg_data['arrival_date'])).days
        return item['price'] * nights

    def process_form_data(self, registration, value, old_data=None, billable_items_locked=False):
        if billable_items_locked and old_data.price:
            # if the old field was paid we can simply ignore any change and keep the old data
            return {}
        data = {}
        if value:
            data = {
                'choice': value['choice'],
                'arrival_date': value['arrivalDate'],
                'departure_date': value['departureDate']
            }
        return super(AccommodationField, self).process_form_data(registration, data, old_data, billable_items_locked)

    def get_places_used(self):
        places_used = Counter()
        for registration in self.form_item.registration_form.active_registrations:
            if self.form_item.id not in registration.data_by_field:
                continue
            data = registration.data_by_field[self.form_item.id].data
            if not data:
                continue
            places_used.update((data['choice'],))
        return dict(places_used)

    def iter_placeholder_info(self):
        yield 'name', 'Accommodation name for "{}" ({})'.format(self.form_item.title, self.form_item.parent.title)
        yield 'nights', 'Number of nights for "{}" ({})'.format(self.form_item.title, self.form_item.parent.title)
        yield 'arrival', 'Arrival date for "{}" ({})'.format(self.form_item.title, self.form_item.parent.title)
        yield 'departure', 'Departure date for "{}" ({})'.format(self.form_item.title, self.form_item.parent.title)

    def render_placeholder(self, data, key=None):
        mapping = {'name': 'choice',
                   'nights': 'nights',
                   'arrival': 'arrival_date',
                   'departure': 'departure_date'}
        rv = self.get_friendly_data(data).get(mapping[key], '')
        if isinstance(rv, date):
            rv = format_date(rv).decode('utf-8')
        return rv


def _to_machine_date(date):
    return datetime.strptime(date, '%d/%m/%Y').strftime('%Y-%m-%d')


def _to_date(date):
    return datetime.strptime(date, '%Y-%m-%d').date()


class MultiChoiceField(ChoiceBaseField):
    name = 'multi_choice'

    @property
    def default_value(self):
        return {}

    def get_friendly_data(self, registration_data):
        def _format_item(uuid, number_of_slots):
            caption = self.form_item.data['captions'][uuid]
            return '{} (+{})'.format(caption, number_of_slots - 1) if number_of_slots > 1 else caption

        reg_data = registration_data.data
        if not reg_data:
            return ''
        return sorted(_format_item(uuid, number_of_slots) for uuid, number_of_slots in reg_data.iteritems())

    def process_form_data(self, registration, value, old_data=None, billable_items_locked=False):
        # TODO: create new data version here if an item from old_data's version
        # was already chosen but doesn't exist anymore (or has a new price) and
        # the user also selected a new item that doesn't exist in the current
        # version yet.

        # always store no-option as empty dict
        if value is None:
            value = {}
        if not billable_items_locked:
            return super(RegistrationFormBillableField, self).process_form_data(registration, value, old_data)
        if old_data.data == value:
            # nothing changed
            # XXX: should we ignore slot changes if extra slots don't pay?
            # probably that needs a js update to keep the slots choice
            # enabled even if the item is paid...
            return {}
        # XXX: This code still relies on the client sending data for the disabled fields.
        # This is pretty ugly but especially in case of non-billable extra slots it makes
        # sense to keep it like this.  If someone tampers with the list of billable fields
        # we detect it any reject the change to the field's data anyway.
        old_choices = {x['id']: x for x in old_data.field_data.versioned_data['choices']}
        new_choices = {x['id']: x for x in self.form_item.versioned_data['choices']}
        old_billable = {uuid: num for uuid, num in old_data.data.iteritems()
                        if old_choices[uuid]['is_billable'] and old_choices[uuid]['price']}
        new_billable = {uuid: num for uuid, num in value.iteritems()
                        if new_choices[uuid]['is_billable'] and new_choices[uuid]['price']}
        if old_billable != new_billable:
            # preserve existing data
            return {}
        else:
            # nothing price-related changed
            # TODO: check item prices (in case there's a change between old/new version)
            # for now we simply ignore field changes in this case (since the old/new price
            # check in the base method will fail)
            return super(MultiChoiceField, self).process_form_data(registration, value, old_data, True)
