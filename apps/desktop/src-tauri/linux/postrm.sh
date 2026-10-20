#!/bin/sh
# remove the pantheon-serve link once the package is gone
if [ ! -e "/usr/lib/Pantheon Studio/runtime/pantheon-serve" ]; then rm -f /usr/bin/pantheon-serve; fi
