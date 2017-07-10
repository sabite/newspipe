import json
import logging

from datetime import datetime
from werkzeug import generate_password_hash
from werkzeug.exceptions import NotFound
from flask import (render_template, flash, session, request,
                   url_for, redirect, current_app)
from flask_babel import gettext, lazy_gettext
from flask_login import LoginManager, logout_user, \
                            login_required, current_user
from flask_principal import (Principal, AnonymousIdentity, UserNeed,
                                 identity_changed, identity_loaded,
                                 session_identity_loader)
from yarl import URL
import requests

import conf
from web.views.common import admin_role, api_role, login_user_bundle
from web.controllers import UserController
from web.forms import SignupForm, SigninForm
from notifications import notifications

Principal(current_app)
# Create a permission with a single Need, in this case a RoleNeed.

login_manager = LoginManager()
login_manager.init_app(current_app)
login_manager.login_view = 'login'
login_manager.login_message = lazy_gettext('Please log in to access this page.')
login_manager.login_message_category = 'info'

logger = logging.getLogger(__name__)


@identity_loaded.connect_via(current_app._get_current_object())
def on_identity_loaded(sender, identity):
    # Set the identity user object
    identity.user = current_user

    # Add the UserNeed to the identity
    if current_user.is_authenticated:
        identity.provides.add(UserNeed(current_user.id))
        if current_user.is_admin:
            identity.provides.add(admin_role)
        if current_user.is_api:
            identity.provides.add(api_role)


@login_manager.user_loader
def load_user(user_id):
    return UserController(user_id, ignore_context=True).get(
            id=user_id, is_active=True)

@current_app.before_request
def before_request():
    if current_user.is_authenticated:
        UserController(current_user.id).update(
                    {'id': current_user.id}, {'last_seen': datetime.utcnow()})

@current_app.route('/login', methods=['GET'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.args.get('code'):
        code = request.args.get('code')
        data = {
            'grant_type': 'authorization_code',
            'client_id': conf.OPENIDC_CLIENT_ID,
            'client_secret': conf.OPENIDC_CLIENT_SECRET,
            'redirect_uri': url_for('login', _external=True),
            'code': code
        }
        r = requests.post(conf.OPENIDC_TOKEN_ENDPOINT, data=data)
        tokens = r.json()
        if 'error' not in tokens:
            headers = {
                'Authorization': 'Bearer ' + tokens['access_token'],
            }
            r = requests.post(conf.OPENIDC_USERINFO_ENDPOINT, headers=headers)
            userinfo = r.json()

            ucontr = UserController()
            try:
                user = ucontr.get(**{'external_id': userinfo['sub']})
                ucontr.update({'id': user.id},
                              {
                               'nickname': userinfo['preferred_username'],
                               'email': userinfo['email'],
                               })
            except NotFound:
                user = ucontr.create(nickname=userinfo['preferred_username'],
                                         email=userinfo['email'],
                                         external_id=userinfo['sub'],
                                         is_active=True,
                                         is_api=True)
            login_user_bundle(user)
            return redirect(url_for('home'))

    auth = URL(conf.OPENIDC_AUTHORIZATION_ENDPOINT)
    auth = auth.with_query(client_id=conf.OPENIDC_CLIENT_ID,
                           redirect_uri=url_for('login', _external=True),
                           response_type='code',
                           scope='openid profile email')
    return render_template('login.html', authorization=auth)


@current_app.route('/logout')
@login_required
def logout():
    # Remove the user information from the session
    logout_user()

    # Remove session keys set by Flask-Principal
    for key in ('identity.name', 'identity.auth_type'):
        session.pop(key, None)

    # Tell Flask-Principal the user is anonymous
    identity_changed.send(current_app, identity=AnonymousIdentity())
    session_identity_loader()

    return redirect(url_for('login'))


@current_app.route('/signup', methods=['GET', 'POST'])
def signup():
    if not conf.SELF_REGISTRATION:
        flash(gettext('Self-registration is disabled.'), 'warning')
        return redirect(url_for('home'))
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = SignupForm()
    if form.validate_on_submit():
        user = UserController().create(nickname=form.nickname.data,
                            email=form.email.data,
                            pwdhash=generate_password_hash(form.password.data))

        # Send the confirmation email
        try:
            notifications.new_account_notification(user)
        except Exception as error:
            flash(gettext('Problem while sending activation email: %(error)s',
                          error=error), 'danger')
            return redirect(url_for('home'))

        flash(gettext('Your account has been created. '
                      'Check your mail to confirm it.'), 'success')

        return redirect(url_for('home'))

    return render_template('signup.html', form=form)
