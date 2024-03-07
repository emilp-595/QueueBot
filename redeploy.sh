#!/bin/bash
docker build --tag mkwii-mogi-queuebot .
docker stop mkwii-mogi-queuebot
docker rm mkwii-mogi-queuebot
docker run -d --name mkwii-mogi-queuebot --restart unless-stopped mkwii-mogi-queuebot