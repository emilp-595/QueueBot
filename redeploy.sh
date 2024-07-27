#!/bin/bash
docker stop mkwii-mogi-queuebot
docker rm mkwii-mogi-queuebot
docker container prune
docker build --tag mkwii-mogi-queuebot .
docker image prune
docker run -d --name mkwii-mogi-queuebot -v ${pwd}\settings_data:/app/settings_data --restart unless-stopped mkwii-mogi-queuebot
