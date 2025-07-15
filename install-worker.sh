SERVICE_ID=worker
SERVICE_NAME="transformirror worker"

USER=transformirror1
SERVICES_DIR=/etc/systemd/system/

sudo tee "$SERVICES_DIR/$SERVICE_ID.service" > /dev/null <<EOL
[Unit]
Description=$SERVICE_NAME
Wants=network-online.target
After=network-online.target
[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/run-worker.sh
User=$USER
Restart=always
[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload

sudo systemctl enable $SERVICE_ID
sudo systemctl start $SERVICE_ID