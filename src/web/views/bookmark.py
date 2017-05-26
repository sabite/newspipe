#! /usr/bin/env python
#-*- coding: utf-8 -*-

# Newspipe - A Web based news aggregator.
# Copyright (C) 2010-2016  Cédric Bonhomme - https://www.cedricbonhomme.org
#
# For more information : https://github.com/newspipe/newspipe
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

__author__ = "Cedric Bonhomme"
__version__ = "$Revision: 0.1 $"
__date__ = "$Date: 2017/05/24 $"
__revision__ = "$Date: 2017/05/24 $"
__copyright__ = "Copyright (c) Cedric Bonhomme"
__license__ = "AGPLv3"

import logging
from werkzeug.exceptions import BadRequest

from flask import Blueprint, render_template, flash, \
                  redirect, request, url_for
from flask_babel import gettext
from flask_login import login_required, current_user
from flask_paginate import Pagination, get_page_args
from sqlalchemy import desc

import conf
from lib.utils import redirect_url
from lib.data import import_pinboard_json
from bootstrap import db
from web.forms import BookmarkForm
from web.controllers import BookmarkController, BookmarkTagController

logger = logging.getLogger(__name__)
bookmarks_bp = Blueprint('bookmarks', __name__, url_prefix='/bookmarks')
bookmark_bp = Blueprint('bookmark', __name__, url_prefix='/bookmark')


@bookmarks_bp.route('/', defaults={'per_page': '50'}, methods=['GET'])
@login_required
def list(per_page):
    "Lists the bookmarks."
    head_titles = ["Bookmarks"]

    page, per_page, offset = get_page_args()
    bookmark_contr = BookmarkController(current_user.id)
    bookmarks = bookmark_contr.read().order_by(desc('time'))

    pagination = Pagination(page=page, total=bookmarks.count(),
                            css_framework='bootstrap3',
                            search=False, record_name='bookmarks',
                            per_page=per_page)

    return render_template('bookmarks.html',
                            head_titles=head_titles,
                            bookmarks=bookmarks.offset(offset).limit(per_page),
                            pagination=pagination)


@bookmark_bp.route('/create', methods=['GET'])
@bookmark_bp.route('/edit/<int:bookmark_id>', methods=['GET'])
@login_required
def form(bookmark_id=None):
    "Form to create/edit bookmarks."
    action = gettext("Add a bookmark")
    head_titles = [action]
    if bookmark_id is None:
        return render_template('edit_bookmark.html', action=action,
                               head_titles=head_titles, form=BookmarkForm())
    bookmark = BookmarkController(current_user.id).get(id=bookmark_id)
    action = gettext('Edit bookmark')
    head_titles = [action]
    form = BookmarkForm(obj=bookmark)
    form.tags.data = ", ".join(bookmark.tags_proxy)
    return render_template('edit_bookmark.html', action=action,
                           head_titles=head_titles, bookmark=bookmark,
                           form=form)


@bookmark_bp.route('/create', methods=['POST'])
@bookmark_bp.route('/edit/<int:bookmark_id>', methods=['POST'])
@login_required
def process_form(bookmark_id=None):
    "Process the creation/edition of bookmarks."
    form = BookmarkForm()
    bookmark_contr = BookmarkController(current_user.id)
    tag_contr = BookmarkTagController(current_user.id)

    if not form.validate():
        return render_template('edit_bookmark.html', form=form)

    bookmark_attr = {'href': form.href.data,
                    'description': form.description.data,
                    'title': form.title.data,
                    'shared': form.shared.data,
                    'to_read': form.to_read.data}

    if bookmark_id is not None:
        tags = []
        for tag in form.tags.data.split(','):
            new_tag = tag_contr.create(text=tag.strip(), user_id=current_user.id,
                                        bookmark_id=bookmark_id)
            tags.append(new_tag)
        bookmark_attr['tags'] = tags
        bookmark_contr.update({'id': bookmark_id}, bookmark_attr)
        flash(gettext('Bookmark successfully updated.'), 'success')
        return redirect(url_for('bookmark.form', bookmark_id=bookmark_id))

    # Create a new bookmark
    new_bookmark = bookmark_contr.create(**bookmark_attr)
    tags = []
    for tag in form.tags.data.split(','):
        new_tag = tag_contr.create(text=tag.strip(), user_id=current_user.id,
                                    bookmark_id=new_bookmark.id)
        tags.append(new_tag)
    bookmark_attr['tags'] = tags
    bookmark_contr.update({'id': new_bookmark.id}, bookmark_attr)
    flash(gettext('Bookmark successfully created.'), 'success')
    return redirect(url_for('bookmark.form', bookmark_id=new_bookmark.id))


@bookmark_bp.route('/delete/<int:bookmark_id>', methods=['GET'])
@login_required
def delete(bookmark_id=None):
    "Delete a bookmark."
    bookmark = BookmarkController(current_user.id).delete(bookmark_id)
    flash(gettext("Bookmark %(bookmark_name)s successfully deleted.",
                  bookmark_name=bookmark.title), 'success')
    return redirect(redirect_url())


@bookmark_bp.route('/bookmarklet', methods=['GET', 'POST'])
@login_required
def bookmarklet():
    bookmark_contr = BookmarkController(current_user.id)
    href = (request.args if request.method == 'GET' else request.form)\
            .get('href', None)
    if not href:
        flash(gettext("Couldn't add bookmark: url missing."), "error")
        raise BadRequest("url is missing")

    bookmark_exists = bookmark_contr.read(**{'href': href}).all()
    if bookmark_exists:
        flash(gettext("Couldn't add bookmark: bookmark already exists."),
                "warning")
        return redirect(url_for('bookmark.form',
                                            bookmark_id=bookmark_exists[0].id))

    bookmark_attr = {'href': href,
                    'description': '',
                    'title': href,
                    'shared': False,
                    'to_read': True}

    new_bookmark = bookmark_contr.create(**bookmark_attr)
    flash(gettext('Bookmark successfully created.'), 'success')
    return redirect(url_for('bookmark.form', bookmark_id=new_bookmark.id))

@bookmark_bp.route('/import_pinboard', methods=['POST'])
@login_required
def import_pinboard():
    bookmarks = request.files.get('jsonfile', None)
    if bookmarks:
        try:
            nb_bookmarks = import_pinboard_json(current_user, bookmarks.read())
            flash(gettext("%(nb_bookmarks)s bookmarks successfully imported.",
                          nb_bookmarks=nb_bookmarks), 'success')
        except Exception as e:
            flash(gettext('Error when importing bookmarks.'), 'error')

    return redirect(redirect_url())