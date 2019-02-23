#! /usr/bin/env python3

import datetime
import fake_useragent
import influxdb
import json
import requests
import time
import sys

import config

class Tado:
  username       = ''
  password       = ''
  useragent      = fake_useragent.UserAgent(cache=False, fallback='Mozilla/5.0 (X11; OpenBSD amd64; rv:28.0) Gecko/20100101 Firefox/28.0')
  headers        = {
    'Referer'    : 'https://my.tado.com',
    'Origin'     : 'https://my.tado.com',
    'User-Agent' : useragent.random
  }
  api            = 'https://my.tado.com/api/v2/homes'
  access_headers = headers
  refresh_token  = ''
  modes          = {
    'HOME'  : 0,
    'AWAY'  : 1,
    'SLEEP' : 2
  }


  def __init__(self, username, password):
    self.username = username
    self.password = password
    self._authenticateBackoff(False)
    self.id = self._getMe()['homes'][0]['id']

  def _authenticate(self, refresh):
    url = 'https://auth.tado.com/oauth/token'
    data = {
      'client_id'     : 'tado-web-app',
      'client_secret' : 'wZaRN7rpjn3FoNyF5IFuxg9uMzYJcvOoQ8QWiIqS3hfk6gLhVlG57j5YNoZL2Rtc',
      'scope'         : 'home.user',
    }
    if refresh:
      headers = self.headers
      data = { **data,
        'grant_type'    : 'refresh_token',
        'refresh_token' : self.refresh_token,
      }
    else:
      headers = self.access_headers
      data = { **data,
        'grant_type'    : 'password',
        'password'      : self.password,
        'username'      : self.username,
      }
    return requests.post(url, data=data, headers=headers).json()

  def _authenticateBackoff(self, refresh):
    retries = 0
    totalBackoff = 0
    response = self._authenticate(refresh)
    while 'access_token' not in response:
      retries += 1
      backoff = retries ** 2
      totalBackoff += backoff
      print('[%s] Authentication failed. Backing off %s seconds...' % (datetime.datetime.now().isoformat(), backoff))
      time.sleep(backoff)

      response = self._authenticate(refresh)
      if refresh and 'access_token' not in response and totalBackoff > 10:
        print('[%s] Authentication failed. Trying new login...' % (datetime.datetime.now().isoformat()))
        response = self._authenticate(False)
      if 'access_token' not in response and totalBackoff > 20:
        print('[%s] Authentication failed too long. Shutting down.' % (datetime.datetime.now().isoformat()))
        sys.exit(1)
    self.refresh_token = response['refresh_token']
    self.access_headers['Authorization'] = 'Bearer ' + response['access_token']

  def _apiCall(self, cmd):
    url = '%s/%i/%s' % (self.api, self.id, cmd)
    return requests.get(url, headers=self.access_headers).json()

  def _getMe(self):
    url = 'https://my.tado.com/api/v2/me'
    return requests.get(url, headers=self.access_headers).json()

  def _getState(self, zone):
    cmd = 'zones/%i/state' % zone
    return self._apiCall(cmd)

  def _getWeather(self):
    cmd = 'weather'
    data = self._apiCall(cmd)
    return {
      'outside_temperature' : data['outsideTemperature']['celsius'],
      'solar_intensity'     : data['solarIntensity']['percentage'],
    }

  def refreshAuth(self):
    self._authenticateBackoff(True)

  def getZone(self, zone):
    state = self._getState(zone)
    current_temperature = float(state['sensorDataPoints']['insideTemperature']['celsius'])
    humidity            = float(state['sensorDataPoints']['humidity']['percentage'])
    heating_power       = float(state['activityDataPoints']['heatingPower']['percentage'])
    tado_mode           = state['tadoMode']
    if state['setting']['power'] == 'ON':
      wanted_temperature = float(state['setting']['temperature']['celsius'])
    else:
      wanted_temperature = current_temperature
    weather = self._getWeather()
    outside_temperature = float(weather['outside_temperature'])
    solar_intensity = float(weather['solar_intensity'])
    return {
      'outside_temperature' : outside_temperature,
      'solar_intensity'     : solar_intensity,
      'current_temperature' : current_temperature,
      'wanted_temperature'  : wanted_temperature,
      'humidity'            : humidity,
      'heating_power'       : heating_power,
      'tado_mode'           : self.modes[tado_mode],
    }

if __name__ == '__main__':

  influxdb_client = influxdb.InfluxDBClient(host=config.influxdb_host, port=config.influxdb_port, database=config.influxdb_database)
  tado = Tado(config.tado_user, config.tado_pass)

  while True:
    tado.refreshAuth()
    measurements = []
    for id, name in config.tado_zones.items():
      result           = { "measurement": config.influxdb_measurement }
      result["tags"]   = { "room": name }
      result["fields"] = tado.getZone(id)
      print('[%s] %s' % (datetime.datetime.now().isoformat(), result))
      measurements.append(result)
    influxdb_client.write_points(measurements)
    time.sleep(15)
