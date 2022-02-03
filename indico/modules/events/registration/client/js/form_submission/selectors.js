// This file is part of Indico.
// Copyright (C) 2002 - 2022 CERN
//
// Indico is free software; you can redistribute it and/or
// modify it under the terms of the MIT License; see the
// LICENSE file for more details.

import {createSelector} from 'reselect';

import {getItemById, getStaticData} from '../form/selectors';

export const getUserInfo = createSelector(
  getStaticData,
  staticData => staticData.userInfo
);

/** Indicates whether we are updating the registration */
export const getUpdateMode = createSelector(
  getStaticData,
  staticData => !!staticData.registrationUuid
);

/** Get the stored field values when editing a registration */
export const getRegistrationData = createSelector(
  getStaticData,
  staticData => staticData.registrationData
);

/** Indicates whether the registration is moderated */
export const getModeration = createSelector(
  getStaticData,
  staticData => staticData.moderated
);

/** Indicates whether this is rendered in the mangement area */
export const getManagement = createSelector(
  getStaticData,
  staticData => staticData.management
);

/** Indicates whether this registration has alreadyt been paid */
export const getPaid = createSelector(
  getStaticData,
  staticData => staticData.paid
);

/** Get the stored value for a field when editing a registration */
export const getFieldValue = createSelector(
  getUpdateMode,
  getRegistrationData,
  (state, id) => getItemById(state, id),
  (updateMode, registrationData, field) =>
    updateMode ? registrationData[field.htmlName] : undefined
);

export const isPaidItemLocked = createSelector(
  getPaid,
  (state, id) => getItemById(state, id),
  (state, id) => getFieldValue(state, id),
  (paid, field, value) => {
    if (!paid) {
      // nothing locked if the registration hasn't been paid yet
      return false;
    } else if (field.price > 0) {
      // easy for fields that have a price themselves
      return true;
    } else if (value !== undefined && field.choices) {
      // we assume that all fields with more complex options have them in `choices`,
      // and that a billable choice will have a `price`. if the old value is undefined,
      // we never disable the field altogether since some choices could in fact be free,
      // and we rely on the field to disable problematic choices
      let choiceIsSelected;
      if (typeof value === 'string') {
        choiceIsSelected = c => c.id === value;
      } else if (value.choice) {
        choiceIsSelected = c => c.id === value.choice;
      } else {
        choiceIsSelected = c => value[c.id] > 0;
      }
      return !!field.choices.find(c => c.price > 0 && choiceIsSelected(c));
    }
    return false;
  }
);
