#!/bin/bash
docker stop mogi-queuebot
docker rm mogi-queuebot
docker container prune
docker build --tag mogi-queuebot .
docker image prune
docker run -d --name mogi-queuebot -v ${pwd}\settings_data:/app/settings_data --restart unless-stopped mogi-queuebot