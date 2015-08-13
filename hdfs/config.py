#!/usr/bin/env python
# encoding: utf-8

"""Configuration module used by CLIs."""

from .client import Client
from .util import HdfsError
from functools import wraps
from imp import load_source
from logging.handlers import TimedRotatingFileHandler
from six.moves.configparser import ParsingError, RawConfigParser
from tempfile import gettempdir
import logging as lg
import os
import os.path as osp
import sys


_logger = lg.getLogger(__name__)


class Config(RawConfigParser):

  """Configuration class.

  :param path: path to configuration file. If no file exists at that location,
    the configuration parser will be empty. If not specified, the value of the
    `HDFSCLI_CONFIG` environment variable is used if it exists, otherwise it
    defaults to `~/.hdfscli.cfg`.
  :param stream_log_level: Stream handler log level, attached to the root
    logger. A false-ish value will disable this handler. This is particularly
    useful with the :func:`catch` which reports exceptions as log messages.

  """

  default_path = osp.expanduser('~/.hdfscli.cfg')
  global_section = 'global'

  def __init__(self, path=None, stream_log_level=None):
    RawConfigParser.__init__(self)
    self._clients = {}
    self.path = path or os.getenv('HDFSCLI_CONFIG', self.default_path)
    if stream_log_level:
      stream_handler = lg.StreamHandler()
      stream_handler.setLevel(stream_log_level)
      fmt = '%(levelname)s\t%(message)s'
      stream_handler.setFormatter(lg.Formatter(fmt))
      lg.getLogger().addHandler(stream_handler)
    if osp.exists(self.path):
      try:
        self.read(self.path)
      except ParsingError:
        raise HdfsError('Invalid configuration file %r.', self.path)
      else:
        self._autoload()
      _logger.info('Instantiated configuration from %r.', self.path)
    else:
      _logger.info('Instantiated empty configuration.')

  def __repr__(self):
    return '<Config(path=%r)>' % (self.path, )

  def get_client(self, alias=None):
    """Load HDFS client.

    :param alias: The client to look up. If not specified, the default alias in
      the command's section will be used (`default.alias`), or an error will be
      raised if the key doesn't exist.

    Further calls to this method for the same alias will return the same client
    instance (in particular, any option changes to this alias will not be taken
    into account).

    """
    if not alias:
      if (
        not self.has_section(self.global_section) or
        not self.has_option(self.global_section, 'default.alias')
      ):
        raise HdfsError('No alias specified and no default alias found.')
      alias = self.get(self.global_section, 'default.alias')
    if not alias in self._clients:
      for suffix in ('.alias', '_alias'):
        section = '%s%s' % (alias, suffix)
        if self.has_section(section):
          options = dict(self.items(section))
          class_name = options.pop('client', 'Client')
          # Massage options.
          if 'timeout' in options:
            timeout = tuple(int(s) for s in options['timeout'].split(','))
            options['timeout'] = timeout[0] if len(timeout) == 1 else timeout
          self._clients[alias] = Client.from_options(options, class_name)
          break
      else:
        raise HdfsError('Alias %r not found in %r.', alias, self.path)
    return self._clients[alias]

  def get_log_file_handler(self, command):
    """Configure and return log handler.

    :param command: The command to load the configuration for. All options will
      be looked up in the `[COMMAND.command]` section. This is currently only
      used for configuring the file handler for logging.

    """
    section = '%s.command' % (command, )
    path = osp.join(gettempdir(), '%s.log' % (command, ))
    level = lg.DEBUG
    if self.has_section(section):
      key = 'log.disable'
      if self.has_option(section, key) and self.getboolean(section, key):
        return None
      if self.has_option(section, 'log.path'):
        path = self.get(section, 'log.path') # Override default path.
      if self.has_option(section, 'log.level'):
        level = getattr(lg, self.get(section, 'log.level').upper())
    file_handler = TimedRotatingFileHandler(
      path,
      when='midnight', # Daily backups.
      backupCount=1,
      encoding='utf-8',
    )
    fmt = '%(asctime)s\t%(name)-16s\t%(levelname)-5s\t%(message)s'
    file_handler.setFormatter(lg.Formatter(fmt))
    file_handler.setLevel(level)
    return file_handler

  def _autoload(self):
    """Load modules to find clients."""

    def _load(suffix, loader):
      """Generic module loader."""
      option = 'autoload.%s' % (suffix, )
      if self.has_option(self.global_section, option):
        entries = self.get(self.global_section, option)
        for entry in entries.split(','):
          module = entry.strip()
          try:
            loader(module)
          except Exception: # pylint: disable=broad-except
            _logger.exception(
              'Unable to load %r defined at %r.',
              module, self.path
            )
            sys.exit(1)


    _load('modules', __import__)
    _load('paths', lambda path: load_source(
      osp.splitext(osp.basename(path))[0],
      path
    ))

def catch(*error_classes):
  """Returns a decorator that catches errors and prints messages to stderr.

  :param \*error_classes: Error classes.

  Also exits with status 1 if any errors are caught.

  """
  def decorator(func):
    """Decorator."""
    @wraps(func)
    def wrapper(*args, **kwargs):
      """Wrapper. Finally."""
      try:
        return func(*args, **kwargs)
      except error_classes as err:
        _logger.error(err)
        sys.exit(1)
      except Exception: # pylint: disable=broad-except
        _logger.exception('Unexpected exception.')
        sys.exit(1)
    return wrapper
  return decorator
