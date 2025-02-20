// This file is part of Indico.
// Copyright (C) 2002 - 2022 CERN
//
// Indico is free software; you can redistribute it and/or
// modify it under the terms of the MIT License; see the
// LICENSE file for more details.

import React from 'react';
import ReactDOM from 'react-dom';
import {Provider} from 'react-redux';

import createReduxStore from 'indico/utils/redux';

import {setFormData} from './actions';
import reducers from './reducers';
import RegistrationFormSubmission from './RegistrationFormSubmission';

export default function setupRegformSubmission(root) {
  const {
    eventId,
    regformId,
    currency,
    management,
    paid,
    moderated = false,
    userInfo = null,
    submitUrl,
    formData,
    registrationData = null,
    registrationUuid = null,
    fileData = null,
  } = root.dataset;

  const initialData = {
    staticData: {
      eventId: parseInt(eventId, 10),
      regformId: parseInt(regformId, 10),
      management: JSON.parse(management),
      paid: paid ? JSON.parse(paid) : false,
      moderated: JSON.parse(moderated),
      userInfo: JSON.parse(userInfo),
      submitUrl,
      registrationData: registrationData ? JSON.parse(registrationData) : {},
      registrationUuid,
      fileData: fileData ? JSON.parse(fileData) : {},
      currency,
    },
  };
  const store = createReduxStore('regform-submission', reducers, initialData);
  store.dispatch(setFormData(JSON.parse(formData)));

  ReactDOM.render(
    <Provider store={store}>
      <RegistrationFormSubmission />
    </Provider>,
    root
  );
}
