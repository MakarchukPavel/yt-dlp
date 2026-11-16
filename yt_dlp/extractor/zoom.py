import json

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    int_or_none,
    js_to_json,
    parse_resolution,
    url_basename,
    url_or_none,
    urljoin,
)
from ..utils.traversal import traverse_obj


class ZoomIE(InfoExtractor):
    IE_NAME = 'zoom'
    _VALID_URL = r'(?P<base_url>https?://(?:[^.]+\.)?zoom\.us/)rec(?:ording)?/(?:play|share)/(?P<id>[\w.-]+)'
    _TESTS = [{
        # share URL, passcode protected
        'url': 'https://us06web.zoom.us/rec/share/BjsPt3W7BHr9LpBdZB08TMEXhXyGLQoYFpLw2_8kaU_DsYda17qdQvjrHP5x2JE6.PJQDozv1QxzBPNEE',
        'only_matching': True,
    }, {
        # share URL
        'url': 'https://us02web.zoom.us/rec/share/hkUk5Zxcga0nkyNGhVCRfzkA2gX_mzgS3LpTxEEWJz9Y_QpIQ4mZFOUx7KZRZDQA.9LGQBdqmDAYgiZ_8',
        'only_matching': True,
    }, {
        # play URL
        'url': 'https://ffgolf.zoom.us/rec/play/qhEhXbrxq1Zoucx8CMtHzq1Z_2YZRPVCqWK_K-2FkEGRsSLDeOX8Tu4P6jtjZcRry8QhIbvKZdtr4UNo.QcPn2debFskI9whJ',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        base_url, video_id = self._match_valid_url(url).group('base_url', 'id')
        webpage = self._download_webpage(url, video_id)

        # Zoom's recording share/play pages are a client-side ("unified player") app that
        # bootstraps itself from a `window.__zm_unified_player__` object and loads the actual
        # media info over a JSON API.
        player_data = self._search_json(
            r'window\.__zm_unified_player__\s*=', webpage, 'player data', video_id,
            transform_source=js_to_json)
        api_base = url_or_none(player_data.get('apiDomain')) or base_url
        play_info_url = urljoin(api_base, player_data['playInfoApi'])

        play_info = self._download_json(
            play_info_url, video_id, note='Downloading play info JSON')['result']

        passcode_page = traverse_obj(play_info, (
            'middlePageInfo', {lambda x: x if x.get('pageType') == 'verifyPasscode' else None}))
        if passcode_page:
            password = self.get_param('videopassword')
            if not password:
                raise ExtractorError(
                    'This recording is protected by a passcode, use the --video-password option',
                    expected=True)
            verify_url = urljoin(api_base, traverse_obj(
                passcode_page, ('operation', 'action', 'postApi', {str})))
            validation = self._download_json(
                verify_url, video_id, note='Validating passcode', errnote='Unable to validate passcode',
                data=json.dumps({'passwd': password}).encode(),
                headers={'Content-Type': 'application/json'})
            if not validation.get('status'):
                raise ExtractorError(
                    validation.get('errorMessage') or 'Wrong passcode', expected=True)
            play_info = self._download_json(
                play_info_url, video_id, note='Re-downloading play info JSON')['result']
        elif traverse_obj(play_info, ('middlePageInfo', 'pageType')):
            raise ExtractorError(
                f'Unable to access this recording: {play_info["middlePageInfo"]["pageType"]}',
                expected=True)

        media_info = traverse_obj(play_info, ('mediaInfo', {dict})) or {}

        formats, seen_paths = [], set()
        sources = list(traverse_obj(media_info, (
            'multipleSourceInfo', {dict.items}, ..., {lambda t: (t[0], traverse_obj(t[1], 'playUrl'))})))
        sources.append(('source', media_info.get('playUrl')))
        for format_id, source_url in sources:
            source_url = url_or_none(source_url)
            if not source_url:
                continue
            # The signed query differs per response, so dedupe on the path instead
            path = source_url.split('?')[0]
            if path in seen_paths:
                continue
            seen_paths.add(path)
            formats.append({
                'url': source_url,
                'format_id': format_id,
                'ext': 'mp4',
                **parse_resolution(self._search_regex(
                    r'_(\d+x\d+)\.mp4', url_basename(source_url), 'resolution', default='')),
            })

        return {
            'id': video_id,
            'formats': formats,
            **traverse_obj(media_info, {
                'title': ('mediaTopic', {str}),
                'uploader': ('ownerName', {str}),
                'duration': ('mediaDuration', {int_or_none}),
                'timestamp': ('createdTime', {int_or_none(scale=1000)}),
            }),
            'http_headers': {
                'Referer': base_url,
            },
        }


class ZoomClipsIE(InfoExtractor):
    IE_NAME = 'zoom:clips'
    _VALID_URL = r'https?://(?:[^.]+\.)?zoom\.us/clips/share/(?P<id>[\w.-]+)'
    _TESTS = [{
        'url': 'https://zoom.us/clips/share/6YEG4i_qS0eiT7UH9zPeYw',
        'md5': '1431f67d2e74a3ff3fec4feadb984777',
        'info_dict': {
            'id': '6YEG4i_qS0eiT7UH9zPeYw',
            'ext': 'mp4',
            'title': 'Test Clip',
            'uploader': 'Hans Müller',
            'duration': 1,
            'release_timestamp': 1781866637,
            'release_date': '20260619',
            'view_count': int,
        },
    }]

    def _real_extract(self, url):
        clip_id = self._match_id(url)
        # The API response sets cloudfront cookies necessary for access to the m3u8 format
        result = self._download_json(
            f'https://zoomclips.zoom.us/nws/marvel/2.0/clips/share/{clip_id}',
            clip_id, note='Downloading share info JSON')['result']
        media_info = result['mediaInfo']

        return {
            'id': clip_id,
            **traverse_obj(media_info, {
                'title': ('mediaTopic', {str}),
                'uploader': ('ownerName', {str}),
                'release_timestamp': ('createdTime', {int_or_none(scale=1000)}),
                'duration': ('mediaDuration', {int_or_none}),
            }),
            'view_count': traverse_obj(result, ('statisticsInfo', 'count', {int_or_none})),
            'formats': [{
                'url': media_info['playUrl'],
                'protocol': 'm3u8_native',
                'ext': 'mp4',
            }],
        }
