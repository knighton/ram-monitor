#!/usr/bin/python
#
# Kill zombie chrome processes (if you kill a lot of tabs in chrome, it doesn't
# clean up the underlying processes).
#
# Warning: restore tabs first.

import os

# Keep the oldest processes.  (Determining exactly how many it is safe to blow
# away?  Ain't nobody got time for that)
SPARE_UP_TO = 5

ss = os.popen('pgrep chrome').read().split()[SPARE_UP_TO:]
os.system('kill -9 %s 2> /dev/null' % ' '.join(ss))
