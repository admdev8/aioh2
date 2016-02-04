import functools
import os
import unittest
import uuid

import asyncio
from h2.connection import H2Connection
from h2.events import RemoteSettingsChanged
from h2.events import SettingsAcknowledged

from aioh2 import H2Protocol


def async_test(timeout=1):
    func = None
    if callable(timeout):
        func = timeout
        timeout = 1

    def _decorator(f):
        @functools.wraps(f)
        def _wrapper(self, *args, **kwargs):
            try:
                return self.loop.run_until_complete(
                    asyncio.wait_for(
                        asyncio.coroutine(f)(self, *args, **kwargs), timeout,
                        loop=self.loop))
            except asyncio.TimeoutError:
                events = []
                while True:
                    try:
                        events.append(self.server.events.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                self.fail('server events: {}'.format(events))

        return _wrapper

    if func is not None:
        return _decorator(func)

    return _decorator


class Server(H2Protocol):
    def __init__(self, test, client_side, *, loop=None):
        super().__init__(client_side, loop=loop)
        test.server = self
        self.events = asyncio.Queue()

    def _event_received(self, event):
        self.events.put_nowait(event)
        super()._event_received(event)


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.path = os.path.join('/tmp', uuid.uuid4().hex)
        self.server = None
        self._server = self.loop.run_until_complete(
            self.loop.create_unix_server(
                lambda: Server(self, False, loop=self.loop), self.path))
        self.loop.run_until_complete(self._setUp())

    def tearDown(self):
        self._server.close()
        os.remove(self.path)
        self.w.close()

    @asyncio.coroutine
    def _setUp(self):
        self.r, self.w = yield from asyncio.open_unix_connection(self.path)
        self.conn = H2Connection()
        self.conn.initiate_connection()
        self.w.write(self.conn.data_to_send())
        events = yield from self._expect_events(2)
        self.assertIsInstance(events[0], RemoteSettingsChanged)
        self.assertIsInstance(events[1], SettingsAcknowledged)

        self.assertIsInstance((yield from self.server.events.get()),
                              RemoteSettingsChanged)
        self.assertIsInstance((yield from self.server.events.get()),
                              SettingsAcknowledged)

    @asyncio.coroutine
    def _expect_events(self, n=1):
        events = []
        self.w.write(self.conn.data_to_send())
        while len(events) < n:
            events += self.conn.receive_data((yield from self.r.read(1024)))
            self.w.write(self.conn.data_to_send())
        self.assertEqual(len(events), n)
        return events