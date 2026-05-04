#!/bin/bash
docker stop mk8dx-mogi-queuebot
docker rm mk8dx-mogi-queuebot
docker container prune
docker build --tag mk8dx-mogi-queuebot .
docker image prune
docker run -d --name mk8dx-mogi-queuebot -v $(pwd)/settings_data:/app/settings_data --restart unless-stopped mk8dx-mogi-queuebot