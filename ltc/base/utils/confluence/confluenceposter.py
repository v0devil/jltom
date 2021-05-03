from xmlrpc import client
from xmlrpc.client import ServerProxy
from xmlrpc.client import Fault

import logging as log

WIKI_URL = None
WIKI_USER = None
WIKI_PASSWORD = None
WIKI_SPACE = None
WIKI_ALL_PAGE = None


class ConfluenceError(Exception):
	pass


class ConfluenceClient(object):
	def __init__(self, base_url, username, password):
		self._client = ServerProxy(base_url + '/rpc/xmlrpc')
		self._username = username
		self._password = password

	def login(self):
		try:
			self._token = self._client.confluence2.login(
				self._username, self._password)
		except Fault as e:
			if 'AuthenticationFailedException' in e.faultString:
				raise ConfluenceError('Confluence login failed.')

	def logout(self):
		self._client.confluence2.logout(self._token)

	def move_page(self, child, parent, dest='append'):
		self._client.confluence2.movePage(
			self._token, child['id'], parent['id'], dest)

	def get_page(self, space, page_name):
		return self._client.confluence2.getPage(self._token, space, page_name)

	def get_children(self, pageid):
		return self._client.confluence2.getChildren(self._token, pageid)

	def post_page(self, page):
		self._client.confluence2.storePage(self._token, page)

	def prepend_page(self, space, page_name, xhtml):
		page = self.get_page(space, page_name)
		page['content'] = page['content'] + xhtml
		self.post_page(page)

	def prepend_page_max_entries(
        self, space, page_name, xhtml, max_entries, separator
    ):
		page = self.get_page(space, page_name)

		pos = 0
		separator_len = len(separator)
		remaining_entries = max_entries - 1
		while remaining_entries > 0:
			pos = page['content'].find(separator, pos)
			if pos == -1:
				break
			pos += separator_len
			remaining_entries -= 1
		else:
			page['content'] = page['content'][:pos]

		page['content'] = xhtml + page['content']
		self.post_page(page)

	def watchpage(self, page, user):
		try:
			self._client.confluence2.watchPageForUser(
				self._token, page['id'], user)
		except:
			log.warning('can not add user {}'.format(user))
			# raven_client.captureException()
			pass

	def get_watcher(self, page):
		# returns list of dictionarys
		watcher_list = self._client.confluence2.getWatchersForPage(
			self._token, page['id'])
		return [watcher['name'] for watcher in watcher_list]
