FROM homeassistant/home-assistant

EXPOSE 8123

ENV TZ="America/New_York"

COPY smartslydr /config/custom_components/smartslydr/