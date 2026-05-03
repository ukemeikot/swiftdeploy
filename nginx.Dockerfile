FROM nginx:latest

# Make /var/log/nginx writable by uid 101 and drop the access/error symlinks
# (which point to /dev/std{out,err}) so nginx writes real files into the
# mounted named volume.
RUN rm -f /var/log/nginx/access.log /var/log/nginx/error.log \
 && chown -R 101:101 /var/log/nginx
