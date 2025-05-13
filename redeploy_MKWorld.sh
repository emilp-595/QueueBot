#!/bin/bash
docker stop mkworld-mogi-queuebot
docker rm mkworld-mogi-queuebot
docker container prune
docker build --tag mkworld-mogi-queuebot .
docker image prune
docker run -d --name mkworld-mogi-queuebot -v $(pwd)/settings_data:/app/settings_data --restart unless-stopped mkworld-mogi-queuebot