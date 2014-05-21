#!/usr/bin/python
#
# For laptops with not enough memory and poor disk performance:
#
# Monitor RAM usage.  Warns first, then enables swap, as it gets too low.  When
# enough memory becomes available, move data back out of swap.
#
# Requires python gflags.

import datetime
import getpass
import gflags
import os
import subprocess
import sys
import time


SHELL_COLORS = 'Black Red Green Yellow Blue Magenta Cyan White'.split()


FLAGS = gflags.FLAGS

# amount of time between checks.
gflags.DEFINE_float('check_interval_sec', 1.0, '')

# levels of memory where it takes some action (in bytes).
gflags.DEFINE_integer('ram_avail_disable_swap', 600 << 20,
                      'Disable swap if more than this much RAM')
gflags.DEFINE_integer('ram_avail_warn', 400 << 20,
                      'Warn if less than this much RAM')
gflags.DEFINE_integer('ram_avail_enable_swap', 350 << 20,
                      'Enable swap if less than this much RAM')

gflags.DEFINE_boolean('log', True, 'Whether to display log lines')

gflags.DEFINE_integer('ram_bar_len', 100,
                      'Length of the RAM bar part of log lines (in characters)')

# characters used in logging.
gflags.DEFINE_string('ram_used_chr',  ':', 'RAM in use signifier character')
gflags.DEFINE_string('ram_cached_chr', '.', 'RAM cached signifier character')
gflags.DEFINE_string('ram_free_chr', ' ', 'RAM free signifier character')
gflags.DEFINE_string('swap_chr', '=', 'swap cached signifier character')

# logging colorization.
gflags.DEFINE_bool('use_color', True, 'Whether to use color in log lines')
gflags.DEFINE_string('ram_bar_ok_color', 'Green', 'OK RAM color')
gflags.DEFINE_string('ram_bar_warn_color', 'Yellow', 'Warning RAM color')
gflags.DEFINE_string('ram_bar_swap_color', 'Red', 'Swap enabled RAM color')
gflags.DEFINE_string('swap_bar_unused_color', 'Green', 'Unused swap color')
gflags.DEFINE_string('swap_bar_used_color', 'Red', 'Used swap color')


gflags.DEFINE_string('nonpriv_user', 'frak',
                     'Spawn windows as a nonprivileged account')


def validate_flags():
  assert 0 < FLAGS.check_interval_sec

  assert (0 <= FLAGS.ram_avail_enable_swap <= FLAGS.ram_avail_warn <=
          FLAGS.ram_avail_disable_swap)

  assert 0 < FLAGS.ram_bar_len

  assert (len(FLAGS.ram_used_chr) == len(FLAGS.ram_cached_chr) ==
          len(FLAGS.ram_free_chr) == len(FLAGS.swap_chr) == 1)

  assert FLAGS.ram_bar_ok_color in SHELL_COLORS
  assert FLAGS.ram_bar_warn_color in SHELL_COLORS
  assert FLAGS.ram_bar_swap_color in SHELL_COLORS
  assert FLAGS.swap_bar_unused_color in SHELL_COLORS
  assert FLAGS.swap_bar_used_color in SHELL_COLORS

  assert FLAGS.nonpriv_user.isalnum()


def make_dict(start_index, ss):
  """(77, ['a', 'b', 'c']) -> {a: 77, b: 78, c: 79}."""
  return dict(zip(ss, xrange(start_index, start_index + len(ss))))


class Restyler(object):
  def __init__(self):
    self.attr2n = make_dict(
        0, 'Reset Bright Dim Underline Blink Reverse Hidden'.split())
    self.fg2n = make_dict(30, SHELL_COLORS)
    self.bg2n = make_dict(40, SHELL_COLORS)

  def make_command(self, attr, fg, bg):
    return '%s[%s;%s;%sm' % (chr(27), self.attr2n[attr], self.fg2n[fg],
                             self.bg2n[bg])

  def make_command_colorize(self, fg):
    return self.make_command('Bright', fg, 'Black')

  def make_command_reset(self):
    return '%s[0m' % (chr(27),)

  def restyle(self, attr, fg, bg, text):
    begin = self.make_command(attr, fg, bg)
    end = self.make_command_reset()
    return '%s%s%s' % (begin, text, end)

  def colorize(self, fg, text):
    begin = self.make_command_colorize(fg)
    end = self.make_command_reset()
    return '%s%s%s' % (begin, text, end)


class ResourceStatset(object):
  def __init__(self, ram_free, ram_cached, ram_total, swap):
    # all are counts of bytes.
    self.ram_free = ram_free
    self.ram_cached = ram_cached
    self.ram_total = ram_total
    self.swap = swap


class StatsetGetter(object):
  def get_meminfo(self):
    """/proc/meminfo -> (field name -> bytes used)."""
    text = open('/proc/meminfo').read()
    lines = text.split('\n')
    name2bytes = {}
    for line in filter(bool, map(lambda s: s.strip(), lines)):
      ss = line.split()
      if len(ss) == 3:
        name, kb, kb_str = ss
        assert kb_str == 'kB'
        sz = int(kb) * 1024
      elif len(ss) == 2:
        name, sz = ss
        sz = int(sz)
      else:
        assert False
      assert name[-1] == ':'
      name = name[:-1]
      assert name not in name2bytes
      name2bytes[name] = sz
    return name2bytes

  def get(self):
    s2d = self.get_meminfo()
    ram_free = s2d['MemFree']
    ram_cached = s2d['Cached']
    ram_total = s2d['MemTotal']
    swap = s2d['SwapTotal'] - s2d['SwapFree']
    r = ResourceStatset(ram_free, ram_cached, ram_total, swap)
    return r


class SysExecutor(object):
  def yell_at_user(self):
    """black window covers screen as a 'kill some tabs, bro' warning."""
    os.system('sudo -u %s xterm -geometry 100x50+600+100 2> /dev/null' %
              (FLAGS.nonpriv_user,))
    os.system('./wash_chrome.py')

  def turn_on_swap(self):
    """enable swapping."""
    subprocess.call(['swapon', '-a'])  # requires root.

  def turn_off_swap(self):
    """disable swapping."""
    subprocess.call(['swapoff', '-a'])  # requires root.


class RamMonitor():
  def __init__(self, restyler, statter, sys):
    self.restyler = restyler
    self.statter = statter
    self.sys = sys

  def display_line(self, stat):
    # get timestring.
    s = str(datetime.datetime.now())
    time_str = s[:s.rfind('.')]

    # get ram indicator bar lengths.
    cached_len = int(float(stat.ram_cached) / stat.ram_total *
                     FLAGS.ram_bar_len)
    free_len = int(float(stat.ram_free) / stat.ram_total * FLAGS.ram_bar_len)
    used_len = FLAGS.ram_bar_len - free_len - cached_len
    ram_bar = '%s%s%s' % (used_len * FLAGS.ram_used_chr,
                          cached_len * FLAGS.ram_cached_chr,
                          free_len * FLAGS.ram_free_chr)

    # get swap indicator bar lengths.
    ram_to_len_ratio = float(stat.ram_total) / FLAGS.ram_bar_len
    swap_len = int(stat.swap / ram_to_len_ratio)
    swap_bar = '%s' % (swap_len * FLAGS.swap_chr,)

    # colorize the ram bar.
    if FLAGS.use_color:
      ram_bar_warn_beginx = FLAGS.ram_bar_len - int(
          float(FLAGS.ram_avail_warn) / stat.ram_total * FLAGS.ram_bar_len)
      ram_bar_swapping_beginx = FLAGS.ram_bar_len - int(
          float(FLAGS.ram_avail_enable_swap) / stat.ram_total *
          FLAGS.ram_bar_len)
      assert (0 <= ram_bar_warn_beginx <= ram_bar_swapping_beginx <=
              FLAGS.ram_bar_len)

      a = ram_bar[:ram_bar_warn_beginx]
      b = ram_bar[ram_bar_warn_beginx:ram_bar_swapping_beginx]
      c = ram_bar[ram_bar_swapping_beginx:]

      a = self.restyler.colorize(FLAGS.ram_bar_ok_color, a)
      b = self.restyler.colorize(FLAGS.ram_bar_warn_color, b)
      c = self.restyler.colorize(FLAGS.ram_bar_swap_color, c)

      ram_bar = '%s%s%s' % (a, b, c)
      swap_bar = self.restyler.colorize(FLAGS.swap_bar_used_color, swap_bar)

    # number indicating how much ram is available.
    ram_avail = stat.ram_free + stat.ram_cached
    ram_avail_str = '%7.2f' % (ram_avail >> 20,)
    if FLAGS.use_color:
      if ram_avail < FLAGS.ram_avail_enable_swap:
        color = FLAGS.ram_bar_swap_color
      elif ram_avail < FLAGS.ram_avail_warn:
        color = FLAGS.ram_bar_warn_color
      else:
        color = FLAGS.ram_bar_ok_color
      ram_avail_str = self.restyler.colorize(color, ram_avail_str)

    # colorized the swap bar.
    swap_str = '%7.2f' % (stat.swap >> 20,)
    if FLAGS.use_color:
      color = (FLAGS.swap_bar_used_color if stat.swap else
               FLAGS.swap_bar_unused_color)
      swap_str = self.restyler.colorize(color, swap_str)

    print '%s [%s] %s / %7.2f mb ram, %s mb swap %s' % (
        time_str, ram_bar, ram_avail_str, stat.ram_total >> 20, swap_str,
        swap_bar)

  def handle(self):
    s = self.statter.get()

    # log if we want to.
    if FLAGS.log:
      self.display_line(s)

    # if we're using swap and we have enough available ram to comfortably stop
    # using swap, disable it.
    if s.swap and (s.swap + FLAGS.ram_avail_disable_swap <
                          s.ram_cached + s.ram_free):
      self.sys.turn_off_swap()

    # if ram usage is too high, complain.
    if s.ram_cached + s.ram_free < FLAGS.ram_avail_warn:
      self.sys.yell_at_user()

    # if ram usage is really too high, enable swap.
    if s.ram_cached + s.ram_free < FLAGS.ram_avail_enable_swap:
      self.sys.turn_on_swap()

  def run(self):
    if getpass.getuser() != 'root':
      print """
WARNING: This script requires root to manage swap usage.  Continue anyway? (y/n)
"""[1:-1],
      s = raw_input()
      if s != 'y':
        return

    while True:
      self.handle()
      time.sleep(FLAGS.check_interval_sec)


if __name__ == '__main__':
  sys.argv = FLAGS(sys.argv)

  validate_flags()

  monitor = RamMonitor(Restyler(), StatsetGetter(), SysExecutor())
  monitor.run()
