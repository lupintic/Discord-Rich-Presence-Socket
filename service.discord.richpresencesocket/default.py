import xbmc, xbmcaddon
import json
import time
import re
import requests
import os

# SERVER CUSTOM SCRIPT
import socket
import threading
import struct

# Compiled regex patterns (global for reuse)
VALID_TAGS = ["I", "B", "LIGHT", "UPPERCASE", "LOWERCASE", "CAPITALIZE", "COLOR"]
TAG_RE = re.compile(r"\[\s*/?\s*(?:" + "|".join(VALID_TAGS) + r")\s*?\]")
CR_RE = re.compile(r"\[\s*/?\s*CR\s*?\]")
COLOR_RE = re.compile(r"\[\s*/?\s*COLOR\s*?.*?\]")

LAST_ACTIVITY = None  # Cache last sent activity as str for quick comparison
LAST_VIDEO_INFO = None  # Cache video info tag
LAST_MEDIA_TYPE = None  # To detect changes
LAST_CLEAN_TITLE = None  # Cache cleaned title to skip regex
DEBUG_LOG = False  # Toggle for logging; set via settings

HEADER = 64
FORMAT = 'utf-8'
DISCONNECT_MESSAGE = "!DISCONNECT"
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow reuse
running = False

def log(msg):
    xbmc.log("[Discord RP] " + msg)

DISCORD_CLIENT_ID = '0'
CLIENT_ID = ['1413362051098873857']

# Caches
POSTER_CACHE = {}
IMDB_CACHE = {}

def get_anime_poster(title):
    if title in POSTER_CACHE:
        return POSTER_CACHE[title]
    
    query = '''
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        coverImage {
          large
        }
        siteUrl
      }
    }
    '''
    variables = {'search': title}
    try:
        response = requests.post('https://graphql.anilist.co', json={'query': query, 'variables': variables}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            poster_url = data.get('data', {}).get('Media', {}).get('coverImage', {}).get('large')
            site_url = data.get('data', {}).get('Media', {}).get('siteUrl')
            if poster_url and site_url:
                POSTER_CACHE[title] = (poster_url, site_url)
                if DEBUG_LOG:
                    log(f"Fetched AniList poster and site for {title}: {poster_url}, {site_url}")
                return (poster_url, site_url)
    except Exception as e:
        if DEBUG_LOG:
            log(f"Error fetching AniList data for {title}: {str(e)}")
    return None

def get_imdb_id(title, year=None, tmdb_id=None):
    cache_key = f"{title}_{year}" if year else title
    if cache_key in IMDB_CACHE:
        return IMDB_CACHE[cache_key]
    
    imdb_id = None
    
    # First, if TMDB ID provided, try scraping TMDB page for IMDb link
    if tmdb_id:
        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}" if 'movie' in LAST_MEDIA_TYPE else f"https://www.themoviedb.org/tv/{tmdb_id}"
        try:
            resp = requests.get(tmdb_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if resp.status_code == 200:
                match = re.search(r'https?://(?:www\.)?imdb\.com/title/(tt\d+)', resp.text)
                if match:
                    imdb_id = match.group(1)
                    if DEBUG_LOG:
                        log(f"Fetched IMDb ID from TMDB for {title}: {imdb_id}")
        except Exception as e:
            if DEBUG_LOG:
                log(f"Error fetching IMDb from TMDB for {title}: {str(e)}")
    
    # If not found, fallback to IMDb search
    if not imdb_id:
        search_query = title
        if year:
            search_query += f" {year}"
        url = f"https://www.imdb.com/find?q={requests.utils.quote(search_query)}&s=tt"
        try:
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if resp.status_code == 200:
                match = re.search(r'/title/(tt\d+)/', resp.text)
                if match:
                    imdb_id = match.group(1)
                    if DEBUG_LOG:
                        log(f"Fetched IMDb ID from search for {title}: {imdb_id}")
        except Exception as e:
            if DEBUG_LOG:
                log(f"Error fetching IMDb ID for {title}: {str(e)}")
    
    if imdb_id:
        IMDB_CACHE[cache_key] = imdb_id
    return imdb_id

def get_tvshow_imdb():
    try:
        result = xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Player.GetItem","params":{"playerid":1,"properties":["tvshowid"]},"id":1}')
        data = json.loads(result)
        tvshowid = data.get('result', {}).get('item', {}).get('tvshowid', -1)
        if tvshowid > -1:
            result = xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"VideoLibrary.GetTVShowDetails","params":{"tvshowid":' + str(tvshowid) + ',"properties":["uniqueid"]},"id":1}')
            data = json.loads(result)
            uniqueids = data.get('result', {}).get('tvshowdetails', {}).get('uniqueid', {})
            imdb_id = uniqueids.get('imdb', '')
            if not imdb_id:
                tmdb_id = uniqueids.get('tmdb', '')
                if tmdb_id:
                    imdb_id = get_imdb_id(data.get('result', {}).get('tvshowdetails', {}).get('label', ''), None, tmdb_id)
            if imdb_id and imdb_id.startswith('tt'):
                return imdb_id
    except Exception as e:
        if DEBUG_LOG:
            log(f"Error fetching TV show IMDb ID: {str(e)}")
    return None

def getShowImage(showTitle):
    if showTitle in AVAILABLE_IMAGES:
        return AVAILABLE_IMAGES[showTitle]
    return "default"

def removeKodiTags(text):
    if DEBUG_LOG:
        log("Removing tags for: " + text)
    text = TAG_RE.sub("", text)
    text = CR_RE.sub(" ", text)
    text = COLOR_RE.sub("", text)
    if DEBUG_LOG:
        log("Removed tags. Result: " + text)
    return text

class ServiceRichPresence:
    def __init__(self):
        self.presence = None
        self.settings = {}
        self.paused = False  # Default to False
        self.connected = False
        self.server_ip = "192.168.0.100"  # Fallback default
        self.server_port = 5050  # Fallback default
        self.updateSettings()
        self.clientId = self.settings['client_id']

    def setPauseState(self, state):
        self.paused = state

    def updateSettings(self):
        try:
            self.settings = {}
            self.settings['large_text'] = "Kodi"

            addon = xbmcaddon.Addon()

            self.settings['episode_state'] = int(addon.getSetting('episode_state') or '3')
            self.settings['episode_details'] = int(addon.getSetting('episode_details') or '0')
            self.settings['movie_state'] = int(addon.getSetting('movie_state') or '0')
            self.settings['movie_details'] = int(addon.getSetting('movie_details') or '0')
            self.settings['client_id'] = int(addon.getSetting('client_id') or '0')
            self.settings['debug_log'] = addon.getSettingBool('debug_log')  # Old format: getSettingBool
            self.settings['inmenu'] = addon.getSettingBool('inmenu')  # Old format: getSettingBool
            self.settings['server_ip'] = addon.getSetting('server_ip') or '192.168.50.77'
            self.settings['server_port'] = int(addon.getSetting('server_port') or '5050')
            self.settings['language'] = addon.getSetting('language') or '0'

            global DEBUG_LOG
            DEBUG_LOG = self.settings['debug_log']

            self.server_ip = self.settings['server_ip']
            self.server_port = self.settings['server_port']
            global server
            try:
                server.close()  # Close existing socket
            except:
                pass
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((self.server_ip, self.server_port))  # Bind to new IP and port
                if DEBUG_LOG:
                    log(f"[DEBUG] Bound server to {self.server_ip}:{self.server_port}")
                else:
                    log(f"[INFO] Server listening on {self.server_ip}:{self.server_port}")
            except Exception as e:
                log(f"[ERROR] Failed to bind server to {self.server_ip}:{self.server_port}: {str(e)}")
                self.connected = False

            if DEBUG_LOG:
                log(str(self.settings))
        except Exception as e:
            log("Error loading settings: " + str(e))
            # Hardcoded fallbacks
            self.settings['episode_state'] = 3
            self.settings['episode_details'] = 0
            self.settings['movie_state'] = 0
            self.settings['movie_details'] = 0
            self.settings['client_id'] = 0
            self.settings['debug_log'] = False
            self.settings['inmenu'] = False
            self.settings['server_ip'] = '192.168.0.100'
            self.settings['server_port'] = 5050
            self.settings['language'] = '0'

    def gatherData(self):
        global LAST_VIDEO_INFO, LAST_MEDIA_TYPE, LAST_CLEAN_TITLE
        player = xbmc.Player()
        if player.isPlayingVideo():
            current_type = xbmc.getInfoLabel('VideoPlayer.dbtype')  # Quick check for media type
            current_title = xbmc.getInfoLabel('VideoPlayer.Title')  # Add title check for changes
            if current_type == LAST_MEDIA_TYPE and current_title == LAST_CLEAN_TITLE:
                return LAST_VIDEO_INFO  # Only reuse if type AND title match
            info = player.getVideoInfoTag()
            LAST_VIDEO_INFO = info
            LAST_MEDIA_TYPE = current_type
            LAST_CLEAN_TITLE = self.getCleanTitle(info.getTitle())  # Update with new clean title
            return info
        else:
            LAST_VIDEO_INFO = None
            LAST_MEDIA_TYPE = None
            LAST_CLEAN_TITLE = None
            return None

    def craftNoVideoState(self, data):
        activity = {
            'assets': {'large_image': 'default', 'large_text': self.settings['large_text']},
            'state': 'In menu'
        }
        return activity

    def getEpisodeState(self, data):
        if self.settings['episode_state'] == 0:
            return '{}x{:02} {}'.format(data.getSeason(), data.getEpisode(), self.getCleanTitle(data.getTitle()))
        if self.settings['episode_state'] == 1:
            return data.getTVShowTitle()
        if self.settings['episode_state'] == 2:
            return data.getGenre()
        if self.settings['episode_state'] == 3:
            return self.getCleanTitle(data.getTitle())
        return None

    def getEpisodeDetails(self, data):
        if self.settings['episode_details'] == 0:
            return data.getTVShowTitle()
        if self.settings['episode_details'] == 1:
            return '{}x{:02} {}'.format(data.getSeason(), data.getEpisode(), self.getCleanTitle(data.getTitle()))
        if self.settings['episode_details'] == 2:
            return data.getGenre()
        return None

    def craftEpisodeState(self, data):
        activity = {'type': 3}
        art_url = xbmc.getInfoLabel('VideoPlayer.Art(tvshow.poster)')
        if not art_url:
            art_url = xbmc.getInfoLabel('VideoPlayer.Art(poster)')
        if not art_url:
            art_url = xbmc.getInfoLabel('VideoPlayer.Art(thumb)')

        show_title = data.getTVShowTitle()
        season = data.getSeason()
        episode = data.getEpisode()
        episode_title = self.getCleanTitle(data.getTitle())

        # Format for scroll trigger: 'Season 01, Episode 01'
        scroll_text = f"Season {season:02}, Episode {episode:02}"

        poster_data = get_anime_poster(show_title)
        if poster_data:
            large_image = poster_data[0]
            activity['assets'] = {'large_image': large_image, 'large_text': scroll_text}
            activity['buttons'] = [{'label': 'View on AniList', 'url': poster_data[1]}]
        else:
            if art_url and art_url.startswith('https://'):
                large_image = art_url
            else:
                large_image = getShowImage(show_title)
            activity['assets'] = {'large_image': large_image, 'large_text': scroll_text}
            imdb_id = get_tvshow_imdb()
            if not imdb_id:
                imdb_id = get_imdb_id(show_title, data.getYear())
            if imdb_id and imdb_id.startswith('tt'):
                activity['buttons'] = [{'label': 'View on IMDb', 'url': f'https://www.imdb.com/title/{imdb_id}/'}]

        state = self.getEpisodeState(data)
        if state:
            activity['state'] = state

        details = self.getEpisodeDetails(data)
        if details:
            activity['details'] = details
        return activity

    def craftMovieState(self, data):
        activity = {'type': 3, 'name': self.getCleanTitle(data.getTitle())}
        art_url = xbmc.getInfoLabel('VideoPlayer.Art(poster)')
        if not art_url:
            art_url = xbmc.getInfoLabel('VideoPlayer.Art(thumb)')

        large_text = self.getCleanTitle(data.getTitle())
        poster_data = get_anime_poster(large_text)
        if poster_data:
            large_image = poster_data[0]
            activity['assets'] = {'large_image': large_image, 'large_text': large_text}
            activity['buttons'] = [{'label': 'View on AniList', 'url': poster_data[1]}]
        else:
            if art_url and art_url.startswith('https://'):
                large_image = art_url
            else:
                large_image = 'default'
            activity['assets'] = {'large_image': large_image, 'large_text': large_text}
            imdb_id = data.getUniqueID('imdb')
            tmdb_id = data.getUniqueID('tmdb')
            if not imdb_id and tmdb_id:
                imdb_id = get_imdb_id(large_text, data.getYear(), tmdb_id)
            if not imdb_id:
                imdb_id = get_imdb_id(large_text, data.getYear())
            if imdb_id and imdb_id.startswith('tt'):
                activity['buttons'] = [{'label': 'View on IMDb', 'url': f'https://www.imdb.com/title/{imdb_id}/'}]

        state = self.getMovieState(data)
        if state:
            activity['state'] = state

        details = self.getMovieDetails(data)
        if details:
            activity['details'] = details
        return activity

    def craftVideoState(self, data):
        title = self.getCleanTitle(data.getTitle() or data.getTagLine() or data.getFile())
        activity = {'type': 3, 'name': title}

        art_url = xbmc.getInfoLabel('VideoPlayer.Art(poster)')
        if not art_url:
            art_url = xbmc.getInfoLabel('VideoPlayer.Art(thumb)')

        poster_data = get_anime_poster(title)
        if poster_data:
            large_image = poster_data[0]
            activity['assets'] = {'large_image': large_image, 'large_text': title}
            activity['buttons'] = [{'label': 'View on AniList', 'url': poster_data[1]}]
        else:
            if art_url and art_url.startswith('https://'):
                large_image = art_url
            else:
                large_image = 'default'
            activity['assets'] = {'large_image': large_image, 'large_text': title}
            imdb_id = data.getUniqueID('imdb')
            tmdb_id = data.getUniqueID('tmdb')
            if not imdb_id and tmdb_id:
                imdb_id = get_imdb_id(title, data.getYear(), tmdb_id)
            if not imdb_id:
                imdb_id = get_imdb_id(title, data.getYear())
            if imdb_id and imdb_id.startswith('tt'):
                activity['buttons'] = [{'label': 'View on IMDb', 'url': f'https://www.imdb.com/title/{imdb_id}/'}]

        activity['details'] = title
        return activity

    def getCleanTitle(self, raw_title):
        global LAST_CLEAN_TITLE
        if LAST_CLEAN_TITLE is None:
            LAST_CLEAN_TITLE = removeKodiTags(raw_title)
        return LAST_CLEAN_TITLE

    def getMovieState(self, data):
        if self.settings['movie_state'] == 0:
            return data.getGenre()
        if self.settings['movie_state'] == 1:
            return self.getCleanTitle(data.getTitle())
        return None

    def getMovieDetails(self, data):
        if self.settings['movie_details'] == 0:
            return self.getCleanTitle(data.getTitle())
        if self.settings['movie_details'] == 1:
            return data.getGenre()
        return None

    def mainLoop(self):
        while not monitor.waitForAbort(10):
            pass
        log("Abort called. Exiting...")
        if self.connected:
            try:
                global running, client
                running = False
                if client:
                    client.close()
                server.close()
            except IOError as e:
                self.connected = False
                log("Error closing connection: " + str(e))

    def updatePresence(self):
        global LAST_ACTIVITY, client
        self.connected = True
        if not self.connected:
            return

        data = self.gatherData()
        activity = None
        player = xbmc.Player()  # Local instance for time queries

        if not data:
            if self.settings['inmenu']:
                activity = self.craftNoVideoState(data)
            else:
                # Force clear send when no video
                activity_json = "{}"
                data_bytes = activity_json.encode('utf-8')
                len_bytes = struct.pack('>I', len(data_bytes))
                if client:
                    try:
                        client.sendall(len_bytes + data_bytes)
                        log(f"Sent clear (length {len(data_bytes)}): {activity_json}")
                        LAST_ACTIVITY = None  # Reset cache after clear
                    except Exception as e:
                        log(f"Send error: {str(e)}")
                        client = None
                else:
                    log("No client connected; skipping clear.")
                return  # Exit early after clear
        else:
            media_type = data.getMediaType()
            if media_type == 'episode':
                activity = self.craftEpisodeState(data)
            elif media_type == 'movie':
                activity = self.craftMovieState(data)
            elif media_type == 'video':
                activity = self.craftVideoState(data)
            else:
                activity = self.craftVideoState(data)
                if DEBUG_LOG:
                    log("Unsupported media type: " + str(media_type) + ". Using workaround")

            # Apply pause/timestamp handling for all media types
            if self.paused:
                activity['assets']['small_image'] = 'paused'  # Commented to preserve scroll
                currentTime = player.getTime()
                hours = int(currentTime / 3600)
                minutes = int(currentTime / 60) - hours * 60
                seconds = int(currentTime) - minutes * 60 - hours * 3600

                fullTime = player.getTotalTime()
                fhours = int(fullTime / 3600)
                fminutes = int(fullTime / 60) - fhours * 60
                fseconds = int(fullTime) - fminutes * 60 - fhours * 3600
                activity['assets']['small_text'] = "{}{:02}:{:02}/{}{:02}:{:02}".format(
                    '{}:'.format(hours) if hours > 0 else '',
                    minutes,
                    seconds,
                    '{}:'.format(fhours) if fhours > 0 else '',
                    fminutes,
                    fseconds
                )
            else:
                currentTime = player.getTime()
                fullTime = player.getTotalTime()
                if fullTime > 0:  # Skip if duration unknown
                    remainingTime = fullTime - currentTime
                    if remainingTime > 0:
                        activity['timestamps'] = {'start': int(time.time() - currentTime), 'end': int(time.time() + remainingTime)}
        
        activity_json = json.dumps(activity)  # Proper JSON serialization
        # Send without cache check for precision
        data_bytes = activity_json.encode('utf-8')
        len_bytes = struct.pack('>I', len(data_bytes))
        if client:
            try:
                client.sendall(len_bytes + data_bytes)
                log(f"Sent activity (length {len(data_bytes)}): {activity_json}")
                LAST_ACTIVITY = activity_json
            except Exception as e:
                log(f"Send error: {str(e)}")
                client = None
        else:
            log("No client connected; skipping send.")

class MyPlayer(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)

    def reset_cache(self):
        global LAST_VIDEO_INFO, LAST_MEDIA_TYPE, LAST_CLEAN_TITLE, LAST_ACTIVITY
        LAST_VIDEO_INFO = None
        LAST_MEDIA_TYPE = None
        LAST_CLEAN_TITLE = None
        LAST_ACTIVITY = None  # Force full update on next presence

    def onPlayBackPaused(self):
        drp.setPauseState(True)
        drp.updatePresence()

    def onAVChange(self):
        drp.updatePresence()

    def onAVStarted(self):
        drp.setPauseState(False)
        drp.updatePresence()

    def onPlayBackEnded(self):
        drp.setPauseState(False)  # Reset paused on end
        self.reset_cache()  # Clear cache for next media
        drp.updatePresence()

    def onPlayBackResumed(self):
        drp.setPauseState(False)
        drp.updatePresence()

    def onPlayBackError(self):
        drp.setPauseState(False)  # Reset on error
        self.reset_cache()
        drp.updatePresence()

    def onPlayBackSeek(self, *args):
        drp.updatePresence()

    def onPlayBackSeekChapter(self, *args):
        drp.updatePresence()

    def onPlayBackStarted(self):
        drp.setPauseState(False)
        self.reset_cache()  # Ensure fresh info on start
        drp.updatePresence()

    def onPlayBackStopped(self):
        drp.setPauseState(False)  # Reset paused on stop
        self.reset_cache()
        drp.updatePresence()

class MyMonitor(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        log("Monitor initialized")

    def onSettingsChanged(self):
        drp.updateSettings()
        drp.updatePresence()

AVAILABLE_IMAGES = []

try:
    AVAILABLE_IMAGES = json.loads(requests.get("https://hiumee.github.io/kodi/custom.json").text)
except Exception:
    pass

client = None

def handle_client(conn, addr):
    try:
        connected = True
        while connected:
            msg = conn.recv(1024).decode(FORMAT)
            if msg == DISCONNECT_MESSAGE:
                connected = False
            print(f"[{addr}] {msg}")
            conn.close()
            global client
            client = None
    except:
        pass

def start():
    global running
    running = True
    try:
        server.listen()
        xbmc.log(f"[LISTENING] Server is listening on {drp.server_ip}:{drp.server_port}")
    except Exception as e:
        xbmc.log(f"[ERROR] Failed to start server on {drp.server_ip}:{drp.server_port}: {str(e)}")
        return
    global client
    while running:
        try:
            conn, addr = server.accept()
            client = conn
            thread = threading.Thread(target=handle_client, args=(conn, addr))
            thread.start()
        except Exception as e:
            xbmc.log(f"[ERROR] Server accept error: {str(e)}")
            server.close()
            break

monitor = MyMonitor()
player = MyPlayer()

drp = ServiceRichPresence()

# Moved from earlier position
server_Thread = threading.Thread(target=start, args=())
server_Thread.start()

drp.updatePresence()
drp.mainLoop()