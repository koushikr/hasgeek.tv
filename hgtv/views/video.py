# -*- coding: utf-8 -*-

import re
from urlparse import urlparse, parse_qs
from socket import gaierror
import requests
from flask import render_template, flash, abort, redirect, Markup, request, escape, jsonify, json
from coaster.views import load_models
from baseframe.forms import render_form, render_redirect, render_delete_sqla, render_message

from hgtv import app
from hgtv.forms import VideoAddForm, VideoEditForm, VideoVideoForm, VideoSlidesForm
from hgtv.models import Channel, Video, Playlist, PlaylistVideo, User, db
from hgtv.models.channel import PLAYLIST_AUTO_TYPE, playlist_auto_types
from hgtv.views.login import lastuser


class DataProcessingError(Exception):
    pass


# helpers
def process_video(video, new=False):
    """
    Get metadata for the video from the corresponding site
    """
    # Parse the video url
    if video.video_url:
        parsed = urlparse(video.video_url)
        # Check video source and get corresponding data
        if parsed.netloc in ['youtube.com', 'www.youtube.com']:
            video_id = parse_qs(parsed.query)['v'][0]
            try:
                r = requests.get('https://gdata.youtube.com/feeds/api/videos/%s?v=2&alt=json' % video_id)
                if r.json is None:
                    raise DataProcessingError("Unable to fetch data")
                else:
                    if new:
                        video.title = r.json['entry']['title']['$t']
                        video.description = escape(r.json['entry']['media$group']['media$description']['$t'])
                for item in r.json['entry']['media$group']['media$thumbnail']:
                    if item['yt$name'] == 'mqdefault':
                        video.thumbnail_url = item['url']  # .replace('hqdefault', 'mqdefault')
                video.video_sourceid = video_id
                video.video_source = u"youtube"
            except requests.ConnectionError:
                raise DataProcessingError("Unable to establish connection")
            except gaierror:
                raise DataProcessingError("Unable to resolve the hostname")
        else:
            raise ValueError("Unsupported video site")

    else:
        raise ValueError("Video URL is missing")


def process_slides(video):
    """
    Get metadata for slides from the corresponding site
    """
    if video.slides_url:
        parsed = urlparse(video.slides_url)
        if parsed.netloc in ['slideshare.net', 'www.slideshare.net']:
            try:
                r = requests.get('http://www.slideshare.net/api/oembed/2?url=%s&format=json' % video.slides_url)
                if r.json:
                    video.slides_source = u'slideshare'
                    video.slides_sourceid = r.json['slideshow_id']
                else:
                    raise DataProcessingError("Unable to fetch data")
            except requests.ConnectionError:
                raise DataProcessingError("Unable to establish connection")
            except gaierror:
                raise DataProcessingError("Unable to resolve the URL")
        elif parsed.netloc in ['speakerdeck.com', 'www.speakerdeck.com']:
            try:
                r = requests.get('http://speakerdeck.com/oembed.json?url=%s' % video.slides_url)
                video.slides_source = u'speakerdeck'
                pattern = u'\Wsrc="//speakerdeck.com/embed/([^\s^"]+)'  # pattern to extract slideid from speakerdeck
                video.slides_sourceid = re.findall(pattern, r.json['html'])[0]
            except requests.ConnectionError:
                raise DataProcessingError("Unable to establish connection")
            except gaierror:
                raise DataProcessingError("Unable to resolve the URL")
        else:
            raise ValueError("Unsupported slides site")
    else:
        # added this line because when user submits empty url, he wants to unlink prev slide url
        video.slides_source, video.slides_sourceid = u'', u''


def is_email(email):
    if len(email) > 7:
        if re.match("^.+\\@(\\[?)[a-zA-Z0-9\\-\\.]+\\.([a-zA-Z]{2,3}|[0-9]{1,3})(\\]?)$", email) != None:
            return True
    return False


def check_user_in_local_db(userinfo):
    # check in local db we have userinfo
    if is_email(userinfo):
        user = User.query.filter_by(email=userinfo).first()
    else:
        user = User.query.filter_by(username=userinfo).first()
    return user


@app.route('/<channel>/<playlist>/new', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='new-video')
def video_new(channel, playlist):
    """
    Add a new video
    """
    form = VideoAddForm()
    if form.validate_on_submit():
        video = Video(playlist=playlist)
        form.populate_obj(video)
        process_video(video, new=True)
        process_slides(video)
        video.make_name()
        playlist.videos.append(video)
        db.session.commit()
        flash(u"Added video '%s'." % video.title, 'success')
        return render_redirect(video.url_for('edit'))
    return render_form(form=form, title="New Video", submit="Add",
        cancel_url=playlist.url_for(), ajax=False)


# Use /view as a temp workaround to a Werkzeug URLmap sorting bug
@app.route('/<channel>/<playlist>/<video>/view', methods=['GET', 'POST'])
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    (Video, {'url_name': 'video'}, 'video'),
    permission='view')
def video_view(channel, playlist, video):
    """
    View video
    """
    if playlist not in video.playlists:
        # This video isn't in this playlist. Redirect to canonical URL
        return redirect(video.url_for())

    #form = PlaylistAddForm()
    #playlists = set(channel.playlists) - set(video.playlists)
    #form.playlist.choices = [(p.id, p.title) for p in playlists]
    #if form.validate_on_submit():
    #    playlist = Playlist.query.get(form.data['playlist'])
    #    playlist.videos.append(video)
    #    db.session.commit()
    #    flash(u"Added video '%s'." % video.title, 'success')
    return render_template('video.html', title=video.title, channel=channel, playlist=playlist, video=video)


@app.route('/<channel>/<playlist>/<video>/edit', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    (Video, {'url_name': 'video'}, 'video'),
    permission='edit')
def video_edit(channel, playlist, video):
    """
    Edit video
    """
    if playlist != video.playlist:
        # This video isn't in this playlist. Redirect to canonical URL
        return redirect(video.url_for('edit'))

    form = VideoEditForm(obj=video)
    formvideo = VideoVideoForm(obj=video)
    formslides = VideoSlidesForm(obj=video)
    form_id = request.form.get('form.id')
    if request.method == "POST":
        if form_id == u'video':  # check whether done button is clicked
            if form.validate_on_submit():
                form.populate_obj(video)
                video.make_name()
                db.session.commit()
                flash(u"Edited video '%s'." % video.title, 'success')
                return render_redirect(video.url_for(), code=303)
        elif form_id == u'video_url':  # check video_url was updated
            if formvideo.validate_on_submit():
                formvideo.populate_obj(video)
                process_video(video, new=False)
                db.session.commit()
                return render_redirect(video.url_for('edit'), code=303)
        elif form_id == u'slide_url':  # check slides_url was updated
            if formslides.validate_on_submit():
                formslides.populate_obj(video)
                process_slides(video)
                db.session.commit()
                return render_redirect(video.url_for('edit'), code=303)
    return render_template('videoedit.html',
        channel=channel,
        playlist=playlist,
        video=video,
        form=form,
        formvideo=formvideo,
        formslides=formslides)


@app.route('/<channel>/<playlist>/<video>/add_speaker', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    (Video, {'url_name': 'video'}, 'video'),
    permission='edit')
def add_speaker(channel, playlist, video):
    """
    Add Speaker to the given video
    """
    if request.method == "POST" and request.json['userinfo']:
        userinfo = request.json['userinfo']
        user = check_user_in_local_db(userinfo)
        #check user has previously logged so we can get his info
        if user:
            playlist = user.playlist_for_speaking_in()
            if not playlist:
                playlist = user.playlist_for_speaking_in(create=True)
            if video not in playlist.videos:
                playlist.videos.append(video)
                to_return = {'message': 'Added %s as speaker' % user.username, 'message_type': 'success'}
            else:
                to_return = {'message': 'Speaker %s is already added' % user.username, 'message_type': 'success'}
        else:
            user = lastuser.getuser(userinfo)
            if user:
                # check user has his channel
                channel = Channel.query.filter_by(userid=user['userid']).first()
                if channel is None:
                    channel = Channel(name=user['name'], title=user['title'], userid=user['userid'])
                    db.session.add(channel)
                    new_playlist = Playlist(channel=channel, auto_type=PLAYLIST_AUTO_TYPE.SPEAKING_IN,
                        title=playlist_auto_types.get(PLAYLIST_AUTO_TYPE.SPEAKING_IN), public=False)
                    new_playlist.videos.append(video)
                    db.session.add(new_playlist)
                    to_return = {'message': 'Added %s as speaker' % user['name'], 'message_type': 'success'}
                else:
                    playlist = Playlist.query.filter_by(channel=channel, auto_type=PLAYLIST_AUTO_TYPE.SPEAKING_IN).first()
                    if playlist is None:
                        playlist = Playlist(channel=channel, auto_type=PLAYLIST_AUTO_TYPE.SPEAKING_IN,
                            title=playlist_auto_types.get(PLAYLIST_AUTO_TYPE.SPEAKING_IN), public=False)
                    if video not in playlist.videos:
                        playlist.videos.append(video)
                        db.session.add(playlist)
                        to_return = {'message': 'Added %s as speaker' % user['name'], 'message_type': 'success'}
                    else:
                        to_return = {'message': 'Speaker %s is already added' % user['name'], 'message_type': 'success'}
            else:
                to_return = {'message': 'Unable to locate the user in our database. Please add user in http://auth.hasgeek.com',
                    'message_type': 'failure'}
        db.session.commit()
        return jsonify(to_return)
    #FIXME: Better error message
    return jsonify({'message': "Error with request type", 'message_type': 'error'})


@app.route('/<channel>/<playlist>/<video>/delete', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    (Video, {'url_name': 'video'}, 'video'),
    permission='delete')
def video_delete(channel, playlist, video):
    """
    Delete video
    """
    if playlist != video.playlist:
        # This video isn't in this playlist. Redirect to canonical URL
        return redirect(video.url_for('delete'))

    return render_delete_sqla(video, db, title=u"Confirm delete",
        message=u"Delete video '%s'? This will remove the video from all playlists it appears in." % video.title,
        success=u"You have deleted video '%s'." % video.title,
    ext=playlist.url_for())


@app.route('/<channel>/<playlist>/<video>/remove', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    (Video, {'url_name': 'video'}, 'video'),
    permission='remove-video')
def video_remove(channel, playlist, video):
    """
    Remove video from playlist
    """
    if playlist not in video.playlists:
        # This video isn't in this playlist
        abort(404)

    # If this is the primary playlist for this video, refuse to remove it.
    if playlist == video.playlist:
        return render_message(title="Cannot remove",
            message=Markup("Videos cannot be removed from their primary playlist. "
                '<a href="%s">Return to video</a>.' % video.url_for()))

    connection = PlaylistVideo.query.filter_by(playlist_id=playlist.id, video_id=video.id).first_or_404()

    return render_delete_sqla(connection, db, title="Confirm remove",
        message=u"Remove video '%s' from playlist '%s'?" % (video.title, playlist.title),
        success=u"You have removed video '%s' from playlist '%s'." % (video.title, playlist.title),
        next=playlist.url_for())
