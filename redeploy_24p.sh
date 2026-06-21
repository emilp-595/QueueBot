#!/bin/bash
docker stop mkworld-24-mogi-queuebot
docker rm mkworld-24-mogi-queuebot
docker container prune
docker build --tag mkworld-24-mogi-queuebot .
docker image prune
docker run -d --name mkworld-24-mogi-queuebot -v $(pwd)/settings_data:/app/settings_data --restart unless-stopped mkworld-24-mogi-queuebot