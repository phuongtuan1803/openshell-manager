"""Minimal outbound Bot API; errors never include URLs, responses or credentials."""
import json
import re
import urllib.request

from .store import private
from .. import configuration
from ..control import require


class TelegramError(ValueError):
    pass


def credentials(path):
    private(path)
    try:
        value = configuration.file(path)
        require(set(value) == {'bot_token', 'user_id', 'chat_id'}, 'Invalid private bot configuration')
        require(isinstance(value['bot_token'], str) and
                re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', value['bot_token']), 'Invalid bot token format')
        require(type(value['user_id']) is int and value['user_id'] > 0
                and type(value['chat_id']) is int and value['chat_id'] != 0, 'Numeric user/chat IDs required')
        return value
    except (ValueError, TypeError, KeyError):
        raise ValueError('Invalid private bot configuration') from None


class Telegram:
    def __init__(self, token, transport=None):
        self._token = token
        self._transport = transport or urllib.request.urlopen

    def call(self, method, **payload):
        require(method in {'getUpdates', 'sendMessage', 'answerCallbackQuery'}, 'Unsupported Telegram method')
        try:
            request = urllib.request.Request('https://api.telegram.org/bot' + self._token + '/' + method,
                                             data=json.dumps(payload).encode(),
                                             headers={'Content-Type': 'application/json'})
            # No redirects: a token-bearing URL must never reach another host.
            transport = self._transport
            if transport is urllib.request.urlopen:
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        return None
                transport = urllib.request.build_opener(NoRedirect).open
            with transport(request, timeout=35) as response:
                result = json.loads(response.read(1024 * 1024))
            if result.get('ok') is not True or 'result' not in result:
                raise TelegramError('Telegram unavailable')
            return result['result']
        except Exception:
            raise TelegramError('Telegram unavailable; no permission changed') from None

    def notify(self, chat, sandbox, row, token):
        scope = json.loads(row['scope'])
        text = (f'Manual review only — {sandbox}\n'
                f"Denied endpoint: {scope['host']}:{scope['port']} ({scope['binary']})\n"
                'Native proposal identity, method/path and risk checks unavailable.\n'
                'Risk: endpoint access may expose source or credentials. No permission can be granted here.\n'
                'Details expires 15 minutes after observation. Review native proposals on the host.')
        result = self.call('sendMessage', chat_id=chat, text=text,
                           link_preview_options={'is_disabled': True},
                           reply_markup={'inline_keyboard': [[{'text': 'Details', 'callback_data': token}]]})
        require(isinstance(result, dict) and type(result.get('message_id')) is int, 'Invalid Telegram message identity')
        return result['message_id']
