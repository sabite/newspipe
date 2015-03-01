import conf
import json
import logging
import requests
import feedparser
import dateutil.parser
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from requests_futures.sessions import FuturesSession
from pyaggr3g470r.lib.utils import default_handler

logger = logging.getLogger(__name__)


def extract_id(entry, keys=[('link', 'link'),
                            ('published', 'retrieved_date'),
                            ('updated', 'retrieved_date')], force_id=False):
    entry_id = entry.get('entry_id') or entry.get('id')
    if entry_id:
        return {'entry_id': entry_id}
    if not entry_id and force_id:
        entry_id = hash("".join(entry[entry_key] for _, entry_key in keys
                                if entry_key in entry))
    else:
        ids = {}
        for entry_key, pyagg_key in keys:
            if entry_key in entry and pyagg_key not in ids:
                ids[pyagg_key] = entry[entry_key]
                if 'date' in pyagg_key:
                    ids[pyagg_key] = dateutil.parser.parse(ids[pyagg_key])\
                                                    .isoformat()
        return ids


class AbstractCrawler:
    __session__ = None

    def __init__(self, auth):
        self.auth = auth
        self.session = self.get_session()
        self.url = conf.PLATFORM_URL

    @classmethod
    def get_session(cls):
        if cls.__session__ is None:
            cls.__session__ = FuturesSession(
                    executor=ThreadPoolExecutor(max_workers=conf.NB_WORKER))
            cls.__session__.verify = False
        return cls.__session__

    def query_pyagg(self, method, urn, data=None):
        if data is None:
            data = {}
        method = getattr(self.session, method)
        return method("%sapi/v1.0/%s" % (self.url, urn),
                      auth=self.auth, data=json.dumps(data,
                                                      default=default_handler),
                      headers={'Content-Type': 'application/json'})


class PyAggUpdater(AbstractCrawler):

    def __init__(self, feed, entries, headers, auth):
        self.feed = feed
        self.entries = entries
        self.headers = headers
        super(PyAggUpdater, self).__init__(auth)

    def to_article(self, entry):
        date = datetime.now()

        for date_key in ('published', 'updated'):
            if entry.get(date_key):
                try:
                    date = dateutil.parser.parse(entry[date_key])
                except Exception:
                    pass
                else:
                    break
        content = ''
        if entry.get('content'):
            content = entry['content'][0]['value']
        elif entry.get('summary'):
            content = entry['summary']

        return {'feed_id': self.feed['id'],
                'entry_id': extract_id(entry).get('entry_id', None),
                'link': entry.get('link', self.feed['site_link']),
                'title': entry.get('title', 'No title'),
                'readed': False, 'like': False,
                'content': content,
                'retrieved_date': date.isoformat(),
                'date': date.isoformat()}

    def callback(self, response):
        try:
            results = response.result().json()
        except Exception:
            logger.exception('something went wront with feed %r %r %r %r',
                             self.feed, self.headers, response.result(),
                             getattr(response.result(), 'data', None))
            return
        logger.debug('%r %r - %d entries were not matched',
                     self.feed['id'], self.feed['title'], len(results))
        for id_to_create in results:
            entry = self.entries[tuple(sorted(id_to_create.items()))]
            try:
                logger.debug('creating %r - %r', entry['title'], id_to_create)
                self.to_article(entry)
            except:
                logger.exception('%r %r %r something failed when parsing %r',
                                 self.feed['title'], self.feed['id'],
                                 self.feed['link'], entry)
            self.query_pyagg('post', 'article', self.to_article(entry))

        now = datetime.now()
        logger.debug('%r %r - updating feed etag %r last_mod %r',
                     self.feed['id'], self.feed['title'],
                     self.headers.get('etag'), now)

        self.query_pyagg('put', 'feed/%d' % self.feed['id'], {'error_count': 0,
                     'etag': self.headers.get('etag', ''),
                     'last_modified': self.headers.get('last-modified', '')})


class FeedCrawler(AbstractCrawler):

    def __init__(self, feed, auth):
        self.feed = feed
        super(FeedCrawler, self).__init__(auth)

    def callback(self, response):
        try:
            response = response.result()
            response.raise_for_status()
        except Exception as error:
            error_count = self.feed['error_count'] + 1
            logger.warn('%r %r - an error occured while fetching feed; bumping'
                        ' error count to %r', self.feed['title'],
                        self.feed['id'], error_count)
            self.query_pyagg('put', 'feed/%d' % self.feed['id'],
                             {'error_count': error_count,
                              'last_error': str(error)})
            return

        if response.status_code == 304:
            logger.debug("%r %r - feed responded with 304",
                         self.feed['id'], self.feed['title'])
            return
        if self.feed['etag'] and response.headers.get('etag') \
                and response.headers.get('etag') == self.feed['etag']:
            logger.debug("%r %r - feed responded with same etag (%d) %r",
                         self.feed['id'], self.feed['title'],
                         response.status_code, self.feed['link'])
            return
        ids, entries = [], {}
        parsed_response = feedparser.parse(response.text)
        for entry in parsed_response['entries']:
            entries[tuple(sorted(extract_id(entry).items()))] = entry
            ids.append(extract_id(entry))
        logger.debug('%r %r - found %d entries %r',
                     self.feed['id'], self.feed['title'], len(ids), ids)
        future = self.query_pyagg('get', 'articles/challenge', {'ids': ids})
        updater = PyAggUpdater(self.feed, entries, response.headers, self.auth)
        future.add_done_callback(updater.callback)


class CrawlerScheduler(AbstractCrawler):

    def __init__(self, username, password):
        self.auth = (username, password)
        super(CrawlerScheduler, self).__init__(self.auth)

    def prepare_headers(self, feed):
        headers = {}
        if feed.get('etag', None):
            headers['If-None-Match'] = feed['etag']
        elif feed.get('last_modified'):
            headers['If-Modified-Since'] = feed['last_modified']
        logger.debug('%r %r - calculated headers %r',
                     feed['id'], feed['title'], headers)
        return headers

    def callback(self, response):
        response = response.result()
        response.raise_for_status()
        feeds = response.json()
        logger.debug('%d to fetch %r', len(feeds), feeds)
        for feed in feeds:
            logger.info('%r %r - fetching resources',
                        feed['id'], feed['title'])
            future = self.session.get(feed['link'],
                                      headers=self.prepare_headers(feed))
            future.add_done_callback(FeedCrawler(feed, self.auth).callback)

    def run(self):
        logger.debug('retreving fetchable feed')
        future = self.query_pyagg('get', 'feeds/fetchable')
        future.add_done_callback(self.callback)
