from soundcloud import SoundCloud, MiniTrack
import json
from pathlib import Path
import datetime
import subprocess
import sys
from bsky_utils import *
import requests
import bs4
import demjson3

class BandcampJSON:
    def __init__(self, body, debugging: bool = False):
        self.body = body
        self.json_data = []

    def generate(self):
        """Grabbing needed data from the page"""
        self.get_pagedata()
        self.get_js()
        return self.json_data

    def get_pagedata(self):
        # print(" Grab pagedata JSON..")
        pagedata = self.body.find('div', {'id': 'pagedata'})['data-blob']
        self.json_data.append(pagedata)

    def get_js(self):
        """Get <script> element containing the data we need and return the raw JS"""
        # print(" Grabbing embedded scripts..")
        embedded_scripts_raw = [self.body.find("script", {"type": "application/ld+json"}).string]
        for script in self.body.find_all('script'):
            try:
                album_info = script['data-tralbum']
                embedded_scripts_raw.append(album_info)
            except Exception:
                continue
        for script in embedded_scripts_raw:
            js_data = self.js_to_json(script)
            self.json_data.append(js_data)

    def js_to_json(self, js_data):
        """Convert JavaScript dictionary to JSON"""
        # print(" Converting JS to JSON..")
        # Decode with demjson first to reformat keys and lists
        decoded_js = demjson3.decode(js_data)
        return demjson3.encode(decoded_js)

def bcPlaylist(playlist_url):

    session = requests.Session()
    response = session.get(playlist_url)

    if not response.ok:
        print("Status code: %s", response.status_code)
        print(f"The Album/Track requested does not exist at: {url}")
        return None, None
    
    try:
        soup = bs4.BeautifulSoup(response.text, "lxml")
    except bs4.FeatureNotFound:
        soup = bs4.BeautifulSoup(response.text, "html.parser")

    bandcamp_json = BandcampJSON(soup, False).generate()
    page_json = {}
    for entry in bandcamp_json:
        page_json = {**page_json, **json.loads(entry)}

    if not (tracklist := traverse(page_json, ['track', 'itemListElement'])):
        print("No tracks found in the playlist.")
        return None, None

    thumbnailUrl = page_json.get('image')

    playlistRecord = {
        "$type": "dev.dreary.tunes.playlist",
        "thumbnail": thumbnailUrl,
        "name": page_json.get('name'),
        "description": page_json.get('description'),
        "createdAt": generate_timestamp(),
        "reference": {
            "source": "Bandcamp",
            "link": page_json.get('url'),
            "id": page_json.get('id')
        }
    }

    # print_json(playlistRecord)

    uploaderInfo = {
        "name": traverse(page_json, ['artist'], ['byArtist', 'name'], ['publisher', 'name']),
        "id": traverse(page_json, ['current', ['band_id', 'selling_band_id']], ['publisher', 'additionalProperty', {'name': 'band_id'}, 'value']),
        "url": traverse(page_json, ['byArtist', '@id'], ['publisher', '@id']),
    }
    trackinfos = page_json['trackinfo']

    tracks = []
    for track in tracklist:
        track = track['item']
        track_id = traverse(track, ['additionalProperty', {'name': 'track_id'}, 'value'])
        trackinfo = traverse(trackinfos, [{'id': track_id}], [{'track_id': track_id}])
        record = {
            "$type": "dev.dreary.tunes.track",
            "title": track.get('name') or trackinfo.get('title'),
            "uploader": uploaderInfo,
            "thumbnail": thumbnailUrl,
            "duration": round(trackinfo['duration']),
            "lyrics": traverse(track, ['recordingOf', 'lyrics', 'text']),
            "url": traverse(track, ['@id'], ['mainEntityOfPage']),
            "id": traverse(track, ['additionalProperty', {'name': 'track_id'}, 'value']),
            "source": "Bandcamp",
            "createdAt": generate_timestamp(),
        }
        # print_json(record)
        tracks.append(record)
    return playlistRecord, tracks

def scPlaylist(playlist_url):
    client = SoundCloud(client_id=None)
    playlist = client.resolve(playlist_url)

    if not playlist.tracks:
        print("No tracks found in the playlist.")
        return None, None

    playlistRecord = {
        "$type": "dev.dreary.tunes.playlist",
        "thumbnail": playlist.artwork_url, # not working as expected, https://soundcloud.com/syzymusic2/sets/mgztop0qnt1x
        "name": playlist.title,
        "description": playlist.description,
        "createdAt": generate_timestamp(),
        "reference": {
            "source": "SoundCloud",
            "link": playlist.permalink_url,
            "id": playlist.id
        }
    }

    tracks = []
    for track in playlist.tracks:
        if isinstance(track, MiniTrack):
            if playlist.secret_token:
                track = client.get_tracks([track.id], playlist.id, playlist.secret_token)[0]
            else:
                track = client.get_track(track.id)
        
        record = {
            "$type": "dev.dreary.tunes.track",
            "title": track.title,
            "uploader": {
                "name": track.user.username,
                "id": str(track.user.id),
                "url": track.user.permalink_url,
            },
            "thumbnail": track.artwork_url, # not working as expected, https://soundcloud.com/syzymusic2/sets/mgztop0qnt1x
            "duration": track.duration // 1000,  # ms to seconds
            "description": track.description,
            "url": track.permalink_url,
            "id": str(track.id),
            "source": "SoundCloud",
            "createdAt": generate_timestamp(),
        }
        # print_json(record)
        tracks.append(record)

    return playlistRecord, tracks

def lastInList(obj, *path):
    if not isinstance(obj, list):
        return None, None
    return traverse(obj, [len(obj) - 1, *path])

def ytPlaylist(playlist_url):

    print("Retrieving YouTube playlist data...")

    result = subprocess.run(["yt-dlp", "-J", playlist_url], capture_output=True, text=True)
    if result.returncode == 0:
        print("Command executed successfully")
    else:
        print("Command failed")

    playlist = json.loads(result.stdout)

    if not playlist.get('entries'):
        print("No tracks found in the playlist.")
        return None, None

    pid = playlist.get('id')

    playlistRecord = {
        "$type": "dev.dreary.tunes.playlist",
        "thumbnail": lastInList(playlist.get("thumbnails"), "url"),
        "name": playlist.get('title'),
        "description": playlist.get('description'),
        "createdAt": generate_timestamp(),
        "reference": {
            "source": "YouTube",
            "link": f'https://www.youtube.com/playlist?list={pid}' if pid else None,
            "id": pid
        }
    }

    tracks = []
    for track in playlist.get('entries'):

        if not track:
            continue

        record = {
            "$type": "dev.dreary.tunes.track",
            "title": track.get('title'),
            "uploader": {
                "name": track.get('uploader'),
                "id": track.get('channel_id'),
                "url": track.get('channel_url'),
            },
            "thumbnail": track.get('thumbnail'),
            "duration": track.get('duration'),
            "description": track.get('description'),
            "url": track.get('webpage_url'),
            "id": track.get('id'),
            "source": "YouTube",
            "createdAt": generate_timestamp(),
        }
        # print_json(record)
        tracks.append(record)

    return playlistRecord, tracks

def processPlaylist(url):
    hostname = url.split('/')[2]
    if 'soundcloud' in hostname:
        return scPlaylist(url)
    if 'bandcamp' in hostname:
        return bcPlaylist(url)
    elif 'youtu' in hostname:
        return ytPlaylist(url)
    else:
        print("Invalid URL")
        return None, None

def findOrCreatePlaylistUri(playlistRecord, did, session, service):
    if not playlistRecord: return None

    print("Searching for existing playlist record matches...")

    existingPlaylistRecords = list_records(did, service, "dev.dreary.tunes.playlist")

    for p in existingPlaylistRecords:
        if not isinstance((ref := traverse(p, ['value', 'reference'])), dict): continue
        if all(k in ref and ref[k] == v for k, v in playlistRecord['reference'].items()):
            playlistUri = p['uri']
            print('No playlist record creation')
            break

    else:
        playlistUri = create_record(session, service, 'dev.dreary.tunes.playlist', playlistRecord)

    return playlistUri

def findOrCreateTrackUri(track, trackRecords, session, service):
    for t in trackRecords:
        if track.get('url') == traverse(t, ['value', 'url']):
            print('Existing match for track record found')
            return t.get('uri')
    return create_record(session, service, 'dev.dreary.tunes.track', track)

def findMatchingTrack(track, trackRecords):
    for t in trackRecords:
        if track.get('url') == traverse(t, ['value', 'url']):
            print('Existing match for track record found')
            return t.get('uri')
    return None
    # return traverse(trackRecords, ['value', {'url': track.get('url')}, 'uri'])

def playlistItemMatch(trackUri, playlistUri, playlistItemRecords):
    for p in playlistItemRecords:
        if trackUri == traverse(p, ['value', 'track']) and playlistUri == traverse(p, ['value', 'playlist']):
            print('Existing match for playlistitem record found')
            return p.get('uri')
    return None

def split_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def applyWrites(session, service, writeRecords):
    if len(writeRecords) == 0: return []
    uri = []
    split = split_list(writeRecords, 200)
    l = len(split)
    for i, records in enumerate(split):
        response = apply_writes_create(session, service, records)
        uri.extend(traverse(response, ['results', 'uri'], get_all=True) or [])
        print(f"{i+1}/{l} applyWrites complete")
    return uri

def filterTrackUri(playlistItemRecords, playlistUri, trackUris):
    # uri = []
    # for p in playlistItemRecords:
    #     if (traverse(p, ['value', 'track']) in trackUris) and (traverse(p, ['value', 'playlist']) == playlistUri):
    #         uri.append(p.get('uri'))
    # return uri
    # traverse(playlistItemRecords, [{'value': {'playlist': playlistUri, 'track': trackUris}}, 'uri'], get_all=True, default=[])
    # filteredItems = filterPlaylistItems(playlistItemRecords, playlistUri, trackUris, returnUri=True)
    filteredItems = traverse(playlistItemRecords, ['value', {'playlist': playlistUri, 'track': trackUris}, 'track'], get_all=True, default=[])
    return [t for t in trackUris if t not in filteredItems]
    
def filterPlaylistItems(playlistItemRecords, playlistUri, trackUris=None, returnUri=False):
    filterObj = {'value': {'playlist': playlistUri}}
    if trackUris:
        filterObj['value']['track'] = trackUris
    path = [filterObj]
    if returnUri:
        path.append('uri')
    return traverse(playlistItemRecords, path, get_all=True, default=[])

def main():
    with open('config.json') as f:
        config = json.load(f)
    HANDLE = config.get('HANDLE')
    PASSWORD = config.get('PASSWORD')
    if not (HANDLE and PASSWORD):
        print('Enter credentials in config.json')
        return
    
    did = resolve_handle(HANDLE)
    service = get_service_endpoint(did)
    session = get_session(did, PASSWORD, service)

    if len(sys.argv) < 1:
        playlist_url = input('Input a URL: ')
        if playlist_url == '': return
    else:
        playlist_url = sys.argv[1]

    playlistRecord, tracks = processPlaylist(playlist_url)
    if not playlistRecord:
        return

    playlistUri = findOrCreatePlaylistUri(playlistRecord, did, session, service)
    if not playlistUri:
        return

    print("Retrieving existing track records...")
    trackRecords = list_records(did, service, "dev.dreary.tunes.track")

    writes = []
    trackUris = []
    trackRecordUrlMap = {url: track["uri"] for track in trackRecords if (url := traverse(track, ['value', 'url']))}
    for track in tracks:
        if (trackUri := trackRecordUrlMap.get(track.get('url'))):
            trackUris.append(trackUri)
        else:
            writes.append(track)

    # writes = []
    # trackUris = []
    # for track in tracks:
    #     trackUri = findMatchingTrack(track, trackRecords)
    #     if trackUri:
    #         trackUris.append(trackUri)
    #     else:
    #         writes.append(track)

    # trackUris.extend(traverse(trackRecords, ['value', {'url': track.get('url')}, 'uri'], get_all=True, default=[]))

    # trackUris = [*(traverse(trackRecords, ['value', {'url': traverse(tracks, ['url'])}, 'uri'], get_all=True, default=[]))]
    # writes = [*(traverse(trackRecords, ['value', {'url': not traverse(tracks, ['url'])}, 'uri'], get_all=True, default=[]))]
    if writes:
        trackUris.extend(applyWrites(session, service, writes))
        print("Track applyWrites complete")
    else:
        print("No track record creation required")

    print("Retrieving existing playlistitem records...")
    playlistItemRecords = list_records(did, service, "dev.dreary.tunes.playlistitem")
    # playlistItemRecords = [p for p in playlistItemRecords if playlistUri == traverse(p, ['value', 'playlist'])]

    writes = []
    # get current playlist count to keep index consistent.
    # this doesn't really work and i'm still not convinced the field is a good idea
    # plus it makes the playlist check in removeMatchingPlaylistItems redundant and we loop twice but w/e
    # ordered lists in atproto are kinda just ugly rn tbh, appview could always use createdAt to break tie but then why even bother
    # playlistRecordCount = len(playlistItemRecords := traverse(playlistItemRecords, ['value', {'playlist': playlistUri}, 'uri'], get_all=True, default=[]))
    # print(playlistItemRecords)
    playlistRecordCount = len(playlistItemRecords := filterPlaylistItems(playlistItemRecords, playlistUri))
    trackUris = filterTrackUri(playlistItemRecords, playlistUri, trackUris)

    index = playlistRecordCount
    for trackUri in trackUris:
        index += 1
        writes.append({
            "$type": "dev.dreary.tunes.playlistitem",
            "playlist": playlistUri,
            "track": trackUri,
            "createdAt": generate_timestamp(),
            "index": index
        })
    if writes:
        applyWrites(session, service, writes)
        print("playlistitem applyWrites complete")
    else:
        print("No playlistitem record creation required")

    # index = 0
    # for track in tracks:
    #     index += 1
    #     # replace with apply_writes_create
    #     # findTrackUri, if trackUri add to create block... but that won't work because i need to make a playlistitem referencing the rkey
    #     # i could make deterministic rkeys but thats bad and i dont wanna
    #     # could at least bundle the playlistitems into a createrecord?
    #     # or maybe i could get creative with the response from createRecords?
    #     # the only way that works is if i trust that the order of the list is sequential, which seems, erm. bad.
    #     trackUri = findOrCreateTrackUri(track, trackRecords, session, service)
    #     if not trackUri:
    #         continue
    #     if playlistItemMatch(trackUri, playlistUri, playlistItemRecords):
    #         continue
    #     item = {
    #         "$type": "dev.dreary.tunes.playlistitem",
    #         "playlist": playlistUri,
    #         "track": trackUri,
    #         "createdAt": generate_timestamp(),
    #         "index": index
    #     }
    #     create_record(session, service, 'dev.dreary.tunes.playlistitem', item)

if __name__ == "__main__":
    main()
