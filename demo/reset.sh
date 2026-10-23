#!/bin/sh
# Fresh copies of the two broken demo projects, one per "laptop", in /tmp/oomi-demo.
# Run it before every rehearsal and before going on stage.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
rm -rf /tmp/oomi-demo
mkdir -p /tmp/oomi-demo
cp -R "$here/invoices" /tmp/oomi-demo/laptop-a-invoices
cp -R "$here/shipping" /tmp/oomi-demo/laptop-b-shipping
echo "Ready:"
echo "  laptop A: cd /tmp/oomi-demo/laptop-a-invoices && OOMI_USER=Ivan oomi"
echo "  laptop B: cd /tmp/oomi-demo/laptop-b-shipping && OOMI_USER=Olga oomi"
