# Alitqan app rules of the HTTPS server (alitqan.nginx.conf; a separate file so another listener can include them).
# Excel imports and documents (the API caps uploads at MAX_UPLOAD_MB, default 50).
client_max_body_size 60m;

gzip on;
gzip_types text/css application/javascript application/json image/svg+xml;

location /api/ {
    rewrite ^/api/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:9797;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    # streamed exports and long reports (CAWP-API/DEPLOY.md "Operational notes")
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}

# In-app softphone signalling (only for users whose softphone is enabled): the browser opens
# wss://<this host>/pbx-ws and nginx forwards the SIP WebSocket to Issabel's /ws on the LAN.
# Needs WebRTC on the PBX (docs/issabel-commissioning.md 4b); SIP_WSS_URL points here.
location = /pbx-ws {
    proxy_pass http://10.10.0.220:8088/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

root /var/www/alitqan;

# `expires`, not add_header: an add_header here would drop the server-level headers above.
location /assets/ {
    expires 1y;
    try_files $uri =404;
}

location / {
    expires -1;
    try_files $uri $uri/ /index.html;
}
