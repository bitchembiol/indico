# This file is part of Indico.
# Copyright (C) 2002 - 2022 CERN
#
# Indico is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see the
# LICENSE file for more details.

from flask import jsonify, request, session
from sqlalchemy.orm import defaultload

from indico.modules.events.payment import payment_event_settings
from indico.modules.events.registration.fields.simple import KEEP_EXISTING_FILE_UUID
from indico.modules.events.registration.models.forms import RegistrationForm
from indico.modules.events.registration.util import (get_event_section_data, get_flat_section_submission_data,
                                                     make_registration_schema, modify_registration)
from indico.util.string import camelize_keys
from indico.web.args import parser


class RegistrationFormMixin:
    """Mixin for single registration form RH."""

    normalize_url_spec = {
        'locators': {
            lambda self: self.regform
        }
    }

    def _process_args(self):
        self.regform = (RegistrationForm.query
                        .filter_by(id=request.view_args['reg_form_id'], is_deleted=False)
                        .options(defaultload('form_items').joinedload('children').joinedload('current_data'))
                        .one())


class RegistrationEditMixin:
    def _get_file_data(self):
        return {r.field_data.field.html_field_name: {'filename': r.filename, 'size': r.size,
                                                     'uuid': KEEP_EXISTING_FILE_UUID}
                for r in self.registration.data
                if r.field_data.field.field_impl.is_file_field and r.storage_file_id is not None}

    def _process_POST(self):
        schema = make_registration_schema(self.regform, management=self.management, registration=self.registration)()
        form_data = parser.parse(schema)

        notify_user = not self.management or form_data.pop('notify_user', False)
        if self.management:
            session['registration_notify_user_default'] = notify_user
        modify_registration(self.registration, form_data, management=self.management, notify_user=notify_user)
        return jsonify({'redirect': self.success_url})

    def _process_GET(self):
        form_data = get_flat_section_submission_data(self.regform, management=self.management,
                                                     registration=self.registration)
        section_data = camelize_keys(get_event_section_data(self.regform, management=self.management,
                                                            registration=self.registration))
        file_data = self._get_file_data()
        registration_data = {r.field_data.field.html_field_name: camelize_keys(r.user_data)
                             for r in self.registration.data
                             if r.user_data is not None or r.field_data.field.html_field_name in file_data}

        # TODO remove with angular
        registration_metadata = {
            'paid': self.registration.is_paid,
            'manager': self.management
        }

        return self.view_class.render_template(self.template_file, self.event,
                                               sections=section_data, regform=self.regform,
                                               form_data=form_data,
                                               payment_conditions=payment_event_settings.get(self.event, 'conditions'),
                                               payment_enabled=self.event.has_feature('payment'),
                                               registration=self.registration,
                                               management=self.management,
                                               paid=self.registration.is_paid,
                                               registration_data=registration_data,
                                               file_data=file_data,
                                               registration_metadata=registration_metadata)
