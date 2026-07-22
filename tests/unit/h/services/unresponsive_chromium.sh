#!/bin/sh
# A stand-in browser binary that starts and then never completes Playwright's startup
# handshake. The Node rendering wrapper consequently runs until something outside it
# stops the process, which makes the wrapper's own wall-clock bound observable.
exec sleep 30
