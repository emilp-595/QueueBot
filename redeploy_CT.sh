#!/bin/bash
docker stop ct-mkwii-mogi-queuebot
docker rm ct-mkwii-mogi-queuebot
docker container prune
docker build --tag ct-mkwii-mogi-queuebot .
docker image prune
docker run -d --name ct-mkwii-mogi-queuebot -v $(pwd)/settings_data:/app/settings_data --restart unless-stopped ct-mkwii-mogi-queuebot
