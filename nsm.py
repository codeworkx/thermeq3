#!/usr/bin/env python
import socket
import base64
import sys
import time
sys.path.insert(0, "/usr/lib/python2.7/bridge/") 
from bridgeclient import BridgeClient
from datetime import timedelta, datetime
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.MIMEBase import MIMEBase
from email import Encoders
from os import getpid, path, rename
from ast import literal_eval
import smtplib
import logging
import traceback
import urllib2
import hashlib
import httplib

class setup: pass
class variables: pass

#
# error handling, primitive but funny
#
##def myHandler(type, value, tb):
##	var.logger.exception("Uncaught exception: {0}".format(str(value)))
	
def redirErr(onoff):
	if onoff:
		var.ferr = open(stp.stderr_log, "a")
		var.original_stderr = sys.stderr
		sys.stderr = var.ferr
		print >> sys.stderr, time.strftime("%H:%M:%S", time.localtime()), "Redirection active"
	else:
		print >> sys.stderr, time.strftime("%H:%M:%S", time.localtime()), "Redirection closed"
		sys.stderr = var.original_stderr
		var.ferr.close()

def llError(err_string):
	err_file = open("/root/nsm.error", "a")
	err_file.write(time.strftime("%H:%M:%S", time.localtime()) + "\t" + err_string + "\r\n")
	err_file.close()
	
#
# helpers
#
def hexify(tmpadr):
	return "".join("%02x" % ord(c) for c in tmpadr)

def getHash(filename):
	checksum = hashlib.md5()
	if path.isfile(filename):
		f = file(filename, "rb")
		while True:
			part = f.read(1024)
			if not part: 
				break
			checksum.update(part)
		f.close()
	return checksum

def getUptime():
	with open("/proc/uptime", "r") as f:
		uptime_seconds = float(f.readline().split()[0])
		return str(timedelta(seconds = uptime_seconds)).split(".")[0]

def incErr():
	tmp = tryRead("errs", 0, False)
	var.value.put(stp.cw["errs"], str(tmp+1))

def logSS(isStart):
	if stp.globalDebugSS: 
		stack = traceback.extract_stack()
		filename, codeline, funcName, text = stack[-2]
		if isStart:
			var.logger.debug(">>> " + str(funcName) + " START")
		else:
	   		var.logger.debug("<<< " + str(funcName) + " STOP")
   
def queueMsg(msg):
	var.logger.debug("Queqing [" + str(msg) + "]")
	var.msgQ.insert(0, msg)
	while len(var.msgQ) > 0:
		var.logger.debug("Message queue=" + str(var.msgQ))
		while not str(var.value.get(stp.cw["msg"])) == "":
			time.sleep(stp.timeout)
		tosend = var.msgQ.pop()
		var.logger.debug("Sending message [" + str(tosend) + "]")
		if tosend == "E":
			var.error = True
			var.err2Clear = True
		elif tosend == "C":
			var.err2Clear = False
			var.err2LastStatus = True
			var.logger.info("Clearing error LED")
		elif tosend == "R":
			saveBridge()
		var.value.put(stp.cw["msg"], str(tosend))
		
def getCMD():
	localcmd = var.value.get(stp.cw["cmd"])
	if localcmd is None:
		return ""
	elif len(localcmd) > 0:
		var.value.put(stp.cw["cmd"], "")
		var.logger.info("received command: [" + localcmd + "]")
	return localcmd

# TBI
def isTime():
	this_now = time.time()
	t_h = this_now.tm_hour
	t_m = this_now.tm_min
	for k in stp.day:
		nf = time.strptime(k[0], "%H:%M")
		hf = nf.tm_hour
		mf = nf.tm_min
		nt = time.strptime(k[1], "%H:%M")
		ht = nt.tm_hour
		mt = nt.tm_min
		if t_h >= hf and t_m >= mf and t_h < ht and t_m < mt:
			return stp.day.index(k)
	return -1

def tryRead(cw, default, save):
	if type(default) is str:
		isNum = False
	else:
		isNum = True
	lcw = stp.cw[cw]

	tmp_str = var.value.get(str(lcw))

	if tmp_str == "None":
		tmp = default
	else:
		if isNum:
			try:
				tmp = int(tmp_str)
			except:
				tmp = default
		else:
			tmp = tmp_str
	if save:
		var.value.put(str(lcw), str(tmp))
	return tmp
	
def readlines(sock, recv_buffer=4096, delim="\r\n"):
	buffer = ""
	data = True
	while data:
		try:
			data = sock.recv(recv_buffer)
			buffer += data
			while buffer.find(delim) != -1:
				line, buffer = buffer.split("\n", 1)
				yield line
		except socket.timeout:
			return
	return

def updateUptime():
	tmp = time.time()
	var.value.put(stp.cw["uptime"], str(getUptime()))
	var.value.put(stp.cw["appuptime"], str(timedelta(seconds = int(tmp - stp.appStartTime))))

def updateAllTimes():
	updateUptime()
	updateCounters(False)
			
def updateStatus(statusMsg):
	var.value.put(stp.cw["status"], str(stp.statMsg[statusMsg]))
	
def sendEmail(sendTxt):
	try:
		server = smtplib.SMTP(stp.mailserver, stp.mailport)
	except Exception, error:
		var.logger.error("Error connecting to mail server " +  str(stp.mailserver) + ":" + str(stp.mailport) + ". Error code: " + str(error))
		var.logger.error("Traceback: " + str(traceback.format_exc()))
	else:
		try:
			server.ehlo()
			if server.has_extn('STARTTLS'):
				server.starttls()
				server.ehlo()
			server.login(stp.fromaddr, stp.frompwd)
			server.sendmail(stp.fromaddr, stp.toaddr, sendTxt)
		except smtplib.SMTPAuthenticationError:
			var.logger.error("Authentification error during sending email.")
		except Exception, error:
			var.logger.error("Error during sending email. Error code: " + str(error))
		else:
			server.quit()
			var.logger.info("Mail was sent.")
			return 0
	return 1

def saveBridge():
	f = open(stp.bridgefile, "w")
	for k, v in stp.cw.iteritems():
		if k != stp.cw["dump"]:
			try:
				tmp = var.value.get(v)
			except:
				tmp = ""
			if tmp == "None" or tmp is None:
				tmp = ""
			f.write(v + "=" + tmp + "\r\n")
	f.close()
	var.logger.debug("Bridge file saved.")
	
def loadBridge():
	if path.exists(stp.bridgefile):
		with open(stp.bridgefile, "r") as f:
			for line in f:
				t = (line.rstrip("\r\n")).split('=')
				if not stp.cw["dump"] in line:
					if t[0] in stp.cw.viewvalues():
						var.value.put(t[0], t[1])
					else:
						var.logger.critical("Error processing bridge file. Codeword: [" + str(t[0]) + "] with value [" + str(t[1]) +"]")
				if t[0] == stp.cw["ht"]:
					# var.logger.debug(str(line) + "/" + str(t))
					try:
						var.ht = literal_eval(t[1])
					except:
						var.ht = {"total": [0, 0.0]}
					# var.logger.debug(str(var.ht))									
			f.close()
		updateAllTimes()
		var.logger.debug("Bridge file loaded.")
		return True
	else:
		return False

# 
# problem prediction routines, if during heating valve didn't change position, something is wrong
#
def	isSame(key):
	tmp = var.dev_log[key][1]
	kv = stp.valves[key][1]
	if kv >= tmp - stp.percentage and kv <= tmp + stp.percentage:
		return True
	else:
		return False 
	
def doDevLogging():
	for k, v in stp.valves.iteritems():
		if var.dev_log.has_key(k):
			if var.heating and isSame(k):
				var.dev_log[k][0] += 1
			var.dev_log[k][1] = v[0]						
		else:
			var.dev_log.update({k:[0, v[0]]})
								

#
# autoupdate routines
#
def downloadFile(filename):
	errstr = ""
	try:
		request = urllib2.urlopen(stp.github + filename)
		response = request.read()
	except urllib2.HTTPError, e:
		errstr += "HTTPError = " + str(e.reason)
	except urllib2.URLError, e:
		errstr += "URLError = " + str(e.reason)
	except httplib.HTTPException, e:
		errstr += "HTTPException"
	except Exception, e:
		errstr += "Exception = " + str(traceback.format_exc())
	else:
		fbase = filename.split(".")[0]
		try:
			f = file(stp.homedir + fbase + ".upd", "wb")
		except Exception, e:
			errstr = "Problem during saving new version. File: " + stp.homedir + fbase + ". Error: " + str(e) + " Traceback: " + str(traceback.format_exc())
		else:
			f.write(response)
			f.close()
			errstr = ""

	if not errstr == "":
		var.logger.error(errstr)
		return False
	request.close()
	return True

def checkUpdate():
	errstr = "Unable to get latest version info - "
	try: 
		request = urllib2.urlopen(stp.github + "autoupdate.data")
		response = request.read().rstrip("\r\n")
	except urllib2.HTTPError, e:
		errstr += "HTTPError = " + str(e.reason)
	except urllib2.URLError, e:
		errstr += "URLError = " + str(e.reason)
	except httplib.HTTPException, e:
		errstr += "HTTPException"
	except Exception, e:
		errstr += "Exception = " + str(traceback.format_exc())
	else:
		errstr = ""
		t = response.split(":")
		
		new_hash = getHash(stp.homedir + str(t[1])).hexdigest()
		if new_hash == "":
			var.logger.error("Can't find file " + str(t[1]))
		else:
			try:
				tmp_ver = int(t[0])
			except Exception, e:
				tmp_ver = 0
			var.logger.debug("Available file: " + str(t[1]) + ", V" + str(tmp_ver) + " with hash " + str(t[3]))
			var.logger.debug("Actual version: " + str(stp.version) + ", hash: " + str(new_hash))
			if new_hash != t[3] and stp.version <= tmp_ver:
				var.logger.info("Downloading new version V" + str(tmp_ver))
				down_result = downloadFile(t[1])
				if down_result:
					var.logger.info("V" + str(tmp_ver) + " downloaded. Hash is " + str(t[3]))
					return 2
				else:
					var.logger.error("Problems downloading new version. Result=" + str(down_result) + ", file=" + str(t[1]))
			else:
				return 1

	if not errstr == "":
		var.logger.error(errstr)
	return 0

def doUpdate():
	logSS(True)
	chk = checkUpdate()
	if chk == 2:
		rename(stp.homedir + "nsm.upd", stp.homedir + "nsm.py")
		temp_key = stp.maxid["sn"]
		body = """<html><body><font face="arial,sans-serif">
		<h1>Device upgrade information.</h1>
		<p>Hello, I'm your thermostat and I have a information for you.<br/>
		Please take a note, that I found new version of my control script and I'll be upgraded in few seconds.</br>
		Resistance is futile :).<br/>
		</p></body></html>"""
		sendWarning("upgrade", temp_key, body)
		queueMsg("R")
	logSS(False)
		                
#
# send this, send that
#
def sendErrorLog():
	logSS(True)
	if path.getsize(stp.stderr_log) > 0:
		devname = stp.devname
		msg = MIMEMultipart()
		msg["From"] = stp.fromaddr
		#msg['To'] = stp.toaddr
		msg["To"] = ''.join(stp.toaddr)
		msg["Subject"] = devname + " log email (thermeq3 device)"

		body = """<html><body><font face="arial,sans-serif">
		<h1>%(a0)s status email.</h1>
		<p>Hello, I'm your thermostat and I sending you this email with error logfile as attachment.<br/>
		</p></body></html>""" % \
		{"a0": str(devname)}
		msg.attach(MIMEText(body, "html"))
	
		part = MIMEBase("application", "octet-stream")
		part.set_payload(open(stp.stderr_log, "rb").read())
		Encoders.encode_base64(part)
		head, tail = path.split(stp.stderr_log)
		part.add_header("Content-Disposition", "attachment; filename=\"" + tail + "\"\"")
		msg.attach(part)

		if sendEmail(msg.as_string()) == 0:
			var.value.put(stp.cw["errs"], "0")
			var.ferr.close()
			var.ferr = open(stp.stderr_log, "w")
	else:
		var.logger.info("Zero sized stderr log file, nothing'll be send")
	logSS(False)
	
def sendStatus():
	logSS(True)
	devname = stp.devname
	valve_pos = int(var.value.get(stp.cw["valve"]))
	error = int(var.value.get(stp.cw["errs"]))
	status = var.value.get(stp.cw["status"])
	interval = int(var.value.get(stp.cw["int"]))
	totalerrs = int(var.value.get(stp.cw["terrs"]))

	uptime_string = getUptime()

	var.value.put(stp.cw["terrs"], str(totalerrs + error))
	msg = MIMEMultipart()
	msg["From"] = stp.fromaddr
	#msg['To'] = stp.toaddr
	msg["To"] = ''.join(stp.toaddr)
	msg["Subject"] = devname + " status email (thermeq3 device)"

	heat_str = var.value.get(stp.cw["htstr"])
	
	body = """<html><body><font face="arial,sans-serif">
	<h1>%(a0)s status email.</h1>
	<p>Hello, I'm your thermostat and I sending you this status email.<br/>
	Actual system status <b>%(a1)s</b> is checked every <b>%(a5)02d</b> seconds
	for valve position of <b>%(a6)02d%%</b>.<br/>
	Total heating time is <b>%(a3)s</b><br/>
	Errors from last status mail: <b>%(a2)s</b><br/>
	Total errors since start: <b>%(a7)d</b><br/>
	Device uptime: <b>%(a8)s</b><br/>
	Application uptime: <b>%(a9)s</b><br/>
	</p></body></html>""" % \
	{'a0': str(devname), \
	 'a1': status, \
	 'a2': str(error), \
	 'a3': heat_str, \
	 'a5': interval, \
	 'a6': valve_pos, \
	 'a7': totalerrs, \
	 'a8': uptime_string, \
	 'a9': str(timedelta(seconds = int(time.time() - stp.appStartTime))) \
	}

	msg.attach(MIMEText(body, "html"))

	if sendEmail(msg.as_string()) == 0:
		var.value.put(stp.cw["errs"], "0")
	logSS(False)

def silence(key, isWin):
	##
	## d_w = key: OW_time(thisnow), isMuted(False), warning/error count(0)
	##
	# is there key in dict?
	dt = datetime.now()
	if not var.d_W.has_key(key):
		# there no key, so its new warning
		var.logger.debug("No key " + str(key) + " in d_W. Key added.")
		if isWin:
			var.d_W.update({key:[stp.devices[key][5], False, 0]})
		else:
			var.d_W.update({key:[dt, False, 0]})
		return 2
	else:
		# yes, there it is, so check if we are silent, if so exit, otherwise reset mute
		## threshold, send every X, muted for X
		## "oww": [10*60, 30*60, 45*60]
		## threshold, muted for X, time.time()
		##	"wrn": [60*60, 60*60, tm]
		if var.d_W[key][1]:
			# yes, we must be silent
			if isWin:
				tmp = var.d_W[key][0] + timedelta(seconds = stp.intervals["oww"][2])
			else:
				tmp = var.d_W[key][0] + timedelta(seconds = stp.intervals["wrn"][1])
			if tmp < dt:
				return 1
			else:
				# silence is over
				var.d_W[key][1] = False
	
	# increment counter of warning for this key
	var.d_W[key][2] += 1
	if var.d_W[key][2] > stp.abnormalCount:
		var.logger.critical("Abnormal count of warnings for device [" + str(key) + "], name [" + str(stp.devices[key][2]) + "]")
		var.d_W[key][2] = 0
	return 0

def itsWarnTime():
	tm = time.time()
	if tm > stp.intervals["wrn"][2]:
		stp.intervals["wrn"][2] = tm + stp.intervals["wrn"][0]
		return True
	else:
		return False

	
def sendWarning(selector, dev_key, body_txt):
	logSS(True)
	## var.logger.debug("sendWarning(" + str(selector) + ", " + str(dev_key) + ", " + str(body_txt) + ")")
	devname = stp.devname
	if selector != "openmax" and selector != "upgrade":
		d = stp.devices[dev_key]
		dn = d[2]
		r = d[3]
		rn = stp.rooms[str(r)]
	sil = silence(dev_key, selector == "window")
	if sil == 1:
		var.logger.debug("Warning for device " + str(dev_key) + " is muted!")
		return

	mutestr = "http://" + stp.myip + "/data/put/command/mute" + str(dev_key)
	msg = MIMEMultipart()
	msg["From"] = stp.fromaddr
	msg["To"] = stp.toaddr
	
	if selector == "window":
		owd = int((datetime.now() - stp.devices[dev_key][5]).total_seconds())
		oww = int((datetime.now() - var.d_W[dev_key][0]).total_seconds())
		if sil == 0 and oww < stp.intervals["oww"][1]:
			var.logger.debug("sendWarning() STOP, condition not met. Trace=" + str(oww) + "/" + str(var.d_W[dev_key][0]))
			return
		msg["Subject"] = "Open window in room " + str(rn[0]) + ". Warning from " + devname + " (thermeq3 device)"
		body = """<html><body><font face="arial,sans-serif">
		<h1>Device %(a0)s warning.</h1>
		<p>Hello, I'm your thermostat and I have a warning for you.<br/>
		Please take a care of window <b>%(a0)s</b> in room <b>%(a1)s</b>.
		Window in this room is now opened more than <b>%(a2)d</b> mins.<br/>
		Threshold for warning is <b>%(a3)d</b> mins.<br/>
		</p><p>You can <a href="%(a4)s">mute this warning</a> for %(a5)s mins. \
		</p></body></html>""" % \
		{'a0': str(dn), \
		 'a1': str(rn[0]), \
		 'a2': int(owd / 60), \
		 'a3': int(stp.intervals["oww"][0] / 60), \
		 'a4': str(mutestr), \
		 'a5': int(stp.intervals["oww"][2] / 60)}
	else:
		if sil == 0 and not rightTime("wrn"):
			logSS(False)
			return
		if selector == "battery":
			msg["Subject"] = "Battery status for device " + str(dn) + ". Warning from " + devname + " (thermeq3 device)"
			body = """<html><body><font face="arial,sans-serif">
			<h1>Device %(a0)s battery status warning.</h1>
			<p>Hello, I'm your thermostat and I have a warning for you.<br/>
			Please take a care of device <b>%(a0)s</b> in room <b>%(a1)s</b>.
			This device have low batteries, please replace batteries.<br/>
			</p><p>You can <a href="%(a2)s">mute this warning</a> for %(a2)s mins. \
			</p></body></html>""" % \
			{'a0': str(dn), \
		 	'a1': str(rn[0]), \
		 	'a2': int(stp.intervals["wrn"][1] / 60)}
		elif selector == "error":
			msg["Subject"] = "Error report for device " + str(dn) + ". Warning from " + devname + " (thermeq3 device)"
			body = """<html><body><font face="arial,sans-serif">
			<h1>Device %(a0)s radio error.</h1>
			<p>Hello, I'm your thermostat and I have a warning for you.<br/>
			Please take a care of device <b>%(a0)s</b> in room <b>%(a1)s</b>.
			This device reports error.<br/>
			</p><p>You can <a href="%(a2)s">mute this warning</a> for %(a2)s mins. \
			</p></body></html>""" % \
			{'a0': str(dn), \
		 	'a1': str(rn[0]), \
		 	'a2': int(stp.intervals["wrn"][1] / 60)}
		elif selector == "openmax":
			msg["Subject"] = "Can't connect to MAX! Cube! Warning from " + devname + " (thermeq3 device)"
			body = body_txt
		elif selector == "upgrade":
			msg["Subject"] = devname + " (thermeq3 device) is going to be upgraded"
			body = body_txt
	
	msg.attach(MIMEText(body, "html"))
	if sendEmail(msg.as_string()) == 0 and selector == "window":
		var.d_W[dev_key][0] = datetime.now()
	logSS(False)
	
#
# logging etc
#
def startLog():
	var.logger = logging.getLogger("thermeq3")
	var.logger.setLevel(logging.DEBUG)

	#var.fh = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=10*(1024*1024), backupCount=10)

	var.fh = logging.FileHandler(stp.log_filename)
	var.fh.setLevel(logging.DEBUG)
	formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%d/%m/%Y %H:%M:%S")
	var.fh.setFormatter(formatter)
	var.logger.addHandler(var.fh)

	var.logger.info("V" + str(stp.version) + " started with PID=" + str(getpid()))
	#logger.debug('debug message')
	#logger.warn('warn message')
	#logger.critical('critical message')

def exportCSV(onoff):
	if onoff == "init":
		if path.exists(stp.csv_log):
			rename(stp.csv_log, stp.place + stp.devname + "_" + time.strftime("%d%m%Y-%H%M%S", time.localtime()) + ".csv")
		var.csv = open(stp.csv_log, "a")
	elif onoff == "headers":
		for k, v in stp.valves.iteritems():
			var.csv.write(stp.devices[k][2] + "," + stp.devices[k][2] + ",")
		var.csv.write("\r\n")
	elif onoff == "close":
		var.csv.close()
		
#
# EQ-3/ELV MAX! communication
#
def openMAX():
	var.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	var.client_socket.settimeout(int(stp.timeout / 2))	
	temp_key = stp.maxid["sn"]

	try:
		var.client_socket.connect((stp.max_ip, 62910))
	except Exception, e:
		incErr()
		var.logger.error("Error opening connection to MAX Cube. Error: " + str(e))
		var.logger.error("Traceback: " + str(traceback.format_exc()))
		body = """<html><body><font face="arial,sans-serif">
		<h1>Device %(a0)s warning.</h1>
		<p>Hello, I'm your thermostat and I have a warning for you.<br/>
		Please take a care of connection to MAX! Cube.</br>
		I can't connect to Cube at address <b>%(a1)s</b>.<br/>
		Error: %(a2)s<br/>
		Traceback: %(a3)s<br/>
		</p></body></html>""" % \
		{'a0': str(stp.devname), \
		 'a1': str(stp.max_ip), \
		 'a2': str(e), \
		 'a3': str(traceback.format_exc()) } 
		sendWarning("openmax", temp_key, body)
		return False
	else:
		if var.d_W.has_key(temp_key):
			var.logger.debug("Key " + str(temp_key) + " in d_W deleted.")
			del var.d_W[temp_key]
		return True

def readMAX(refresh):
	var.client_socket.settimeout(int(stp.timeout / 3))
	var.error = False
	this_now = datetime.now()
	for line in readlines(var.client_socket):
		data = line
		sd = data[2:].split(",")
		
		if data[0] == 'H':
			stp.maxid["sn"] = sd[0]
			stp.maxid["rf"] = sd[1]
			stp.maxid["fw"] = sd[2]
		elif data[0] == 'M':
			es = base64.b64decode(sd[2])
			room_num = ord(es[2])
			es_pos = 3
			for i in range(0, room_num):
				room_id = str(ord(es[es_pos]))
				room_len = ord(es[es_pos+1])
				es_pos += 2
				room_name = es[es_pos:es_pos + room_len]
				es_pos += room_len
				room_adr = es[es_pos:es_pos+3]
				es_pos += 3
				if not stp.rooms.has_key(room_id) or refresh:
					stp.rooms.update({room_id:[room_name, hexify(room_adr), False]})

			dev_num = ord(es[es_pos])
			es_pos += 1
			for i in range(0, dev_num):
				dev_type = ord(es[es_pos])
				es_pos += 1
				dev_adr = hexify(es[es_pos:es_pos+3])
				es_pos += 3
				dev_sn = es[es_pos:es_pos+10]
				es_pos += 10
				dev_len = ord(es[es_pos])
				es_pos += 1
				dev_name = es[es_pos:es_pos+dev_len]
				es_pos += dev_len
				dev_room = ord(es[es_pos])
				es_pos += 1
				if not stp.devices.has_key(dev_adr) or refresh:
					#                            type      serial  name      room     OW, OW_time, status, info, temp offset
					stp.devices.update({dev_adr:[dev_type, dev_sn, dev_name, dev_room, 0, this_now, 0, 0, 7]})
		elif data[0] == 'C':
			es = base64.b64decode(sd[1])
			if ord(es[0x04]) == 1:
				dev_adr = hexify(es[0x01:0x04])
				stp.devices[dev_adr][8] = es[0x16]
		elif data[0] == 'L':
			es = base64.b64decode(sd[0])
			es_pos = 0
			while (es_pos < len(es)):
				dev_len = ord(es[es_pos]) + 1
				valve_adr = hexify(es[es_pos+1:es_pos+4])
				valve_status = ord(es[es_pos + 0x05])
				valve_info = ord(es[es_pos + 0x06])
				valve_temp = 0xFF
				valve_curtemp = 0xFF
				# WallMountedThermostat (dev_type 3)
				if dev_len == 13:
					if valve_info & 3 != 2:
						valve_temp = float(int(hexify(es[es_pos + 0x08]), 16)) / 2 # set temp
						valve_curtemp = float(int(hexify(es[es_pos + 0x0C]), 16)) / 10 # measured temp
				# HeatingThermostat (dev_type 1 or 2)
				elif dev_len == 12:
					valve_pos = ord(es[es_pos + 0x07])
					if valve_info & 3 != 2:
						valve_temp = float(int(hexify(es[es_pos + 0x08]), 16)) / 2 # set temp
						valve_curtemp = float(int(hexify(es[es_pos + 0x0A]), 16)) / 10 # measured temp
					stp.valves.update({valve_adr:[valve_pos, valve_temp]})
				elif dev_len == 7:
					tmp_open = ord(es[es_pos + 0x06]) & 2
					if tmp_open != stp.devices[valve_adr][4]:
						tmp_txt = "Window contact " + str(stp.devices[valve_adr][2]) + " is now "
						if tmp_open == 0:
							var.logger.info(tmp_txt + "closed.")
							if var.d_W.has_key(valve_adr):
								var.logger.debug("Key " + str(valve_adr) + " in d_W deleted.")
								del var.d_W[valve_adr]
								# now check for window closed ignore interval, so don't heat X seconds after closing window
								if var.ignore_time > 0 and not var.d_ignore.has_key(valve_adr):
									var.d_ignore.update({valve_adr: time.time() + var.ignore_time * 60})
						else:
							var.logger.info(tmp_txt + "opened.")
						stp.devices[valve_adr][4] = tmp_open
						stp.devices[valve_adr][5] = datetime.now()
				stp.devices[valve_adr][6] = valve_status
				stp.devices[valve_adr][7] = valve_info
				es_pos += dev_len

						
def closeMAX():
	var.client_socket.close()

#
# some stupid commands :)
#
def updateCounters(heatStart):
	# save the date 
	nw = datetime.date(datetime.now()).strftime("%d-%m-%Y")
	tm = time.time()
	
	# update total heat counter	
	if var.heating:
		tmp = var.ht["total"][0]
		tmp += int(time.time() - var.ht["total"][1])
		var.value.put(stp.cw["ht"], str(var.ht))
		var.value.put(stp.cw["htstr"],  str(timedelta(seconds = tmp)))
		var.logger.info("Total heat counter updated to " + str(timedelta(seconds = tmp)))
		var.ht["total"][0] = tmp
		var.ht["total"][1] = time.time()

	# is there a key for today?
	if var.ht.has_key(nw):
		if heatStart:
			var.ht[nw][1] = tm
		elif var.heating:
			totalheat = int(var.ht[nw][0] + (tm - var.ht[nw][1]))
			var.ht[nw] = [totalheat, time.time()]
			var.value.put(stp.cw["ht"], str(var.ht))
			var.value.put(stp.cw["daily"], str(timedelta(seconds = totalheat)))
	else:		                                                                                                 
		if len(var.ht) > 1:
			# if there a key, this must be old key(s)
			# save the old date, and flush values into log
			for k in var.ht.keys():
				v = var.ht[k]
				if not k == "total":
					var.logger.info("Daily heating summary for day: " + str(k) + " is " + str(timedelta(seconds = v[0])))
					var.logger.debug("Deleting old daily heat key: " + str(k))
					del var.ht[k]
		# create the new key
		var.logger.debug("Creating new daily heat key: " + str(nw))
		var.ht.update({nw:[0, time.time()]})
		# so its a new day, update other values 
		# day readings warning, take number of heated readings and divide by 2 
		drW = var.heatReadings / 2
		var.logger.debug("Day reading warnings value=" + str(drW))
		for k, v in var.dev_log.iteritems():
			var.logger.debug("Valve: " + str(k) + " has value " + str(v[0]))
			if v[0] > drW:
				var.logger.info("Valve: " + str(k) + " reports during heating too many same % positions, e.g. " + str(v[0]) + " per " + str(drW))  
			var.dev_log[k][0] = 0
		var.heatReadings = 0
		saveBridge()


def dumpMAX(method):
	logSS(True)
	txt = "DEVICES={"
	for k, v in stp.devices.iteritems():
		txt += str(k).upper() + ":" + str(v) + ", "
	txt += "}; VALVES={"
	for k, v in stp.valves.iteritems():
		txt += str(k).upper() + ":" + str(v) + ", "
	txt += "}; DEV_LOG={"
	for k,v in var.dev_log.iteritems():
		txt += str(k).upper() + ":" + str(v) + ", "
	txt += "}"
	if method == 1:
		var.logger.debug(txt)
	else:
		var.value.put(stp.cw["dump"], txt)
	var.logger.info("System dumped into bridge variable")
	logSS(False)

def readMAXData(refresh):
	logSS(True)
	if not openMAX():
		queueMsg("E")
	else:
		readMAX(refresh)
		if var.heating:
			logstr = "Heating"
		else:
			logstr = "Idle"
		logstr += ", switching at " + str(stp.valve_switch) + "%"
		var.logger.debug(logstr)			
		logstr = "Actual positions follows"
		var.csv.write(time.strftime("%d/%m/%Y %H:%M:%S", time.localtime()) + ",")
		for k, v in stp.valves.iteritems():
			logstr += "\r\n[" + str(k) + "] " + '{:<20}'.format(str(stp.devices[k][2])) + "@" + '{:>3}'.format(str(v[0])) + "%, " + \
				'{:>5}'.format(str(float(v[1] / 10))) + " C"
			var.csv.write(str(v[0]) + "," + str(float(v[1]) / 10) + ",")
		var.csv.write("\r\n")
		var.logger.debug(logstr)
	closeMAX()
	logSS(False)
	
#
# and here we go, this is app logic
#

def isWinOpen(key):
	v = stp.devices[key]
	if v[0] == 4 and v[4] == 2:
		return True
	else:
		return False

def isWinOpenTooLong(key):
	v = stp.devices[key]
	if isWinOpen(key):
		tmp = (datetime.now() - v[5]).total_seconds()
		if tmp > stp.intervals["oww"][0]:
			return True
		else:
			return False
			
def isBattError(key):
	v = stp.devices[key]
	if v[7] & 128 == 128:
		return True
	else:
		return False

def isRadioError(key):
	v = stp.devices[key]
	if v[6] & 8 == 8:
		return True
	else:
		return False

def getTotal():
	if stp.preference == "total":
		return stp.total_switch
	elif stp.preference == "per":
		return stp.per_switch * len(stp.valves)
	else:
		return 10000

def doheat(heatOrNot):
	if heatOrNot:
		var.ht["total"][1] = time.time()
		queueMsg("H")
		updateStatus("heat")
	else:
		queueMsg("S")
		updateStatus("idle")
	updateCounters(heatOrNot)
	var.heating = heatOrNot
		
def doControl():
	heat = False
	valve_count = 0
	stp.total = getTotal()
		
	for k, v in stp.devices.iteritems():
		if isWinOpenTooLong(k): 
			var.logger.debug("Warning condition for window " + str(k) + " met")
			if var.no_oww == 0:
				sendWarning("window", k, "")
		if isBattError(k): 
			sendWarning("battery", k, "")
		if isRadioError(k):
			sendWarning("error", k, "")
	
	grt = 0
	tm = time.time()
	for k, v in stp.valves.iteritems():
		its_ok = False 
		if v[0] > stp.valve_switch:
			if var.d_ignore.has_key(k):
				if var.d_ignore[k] < tm:
					del var.d_ignore[k]
					its_ok = True
			else:
				its_ok = True
			if its_ok:
				heat = True
				valve_key = k
				valve_count += 1
				grt += v[0]
	if not heat and grt >= stp.total:
			heat = True
			valve_count = stp.valve_num
	if heat:
		var.heatReadings += 1
	if var.err2Clear and not var.error:
		queueMsg("C")
	if var.err2LastStatus:
		var.err2LastStatus = False
		if var.heating:
			queueMsg("H")
			var.logger.info("Resuming heating state on status LED")
	# var.logger.debug("Control: " + str(var.heating) + "=" + str(heat) + ", " + str(grt) + "; " + str(stp.valve_num) + " , " + str(valve_count))
	if heat != var.heating:
		if heat and valve_count >= stp.valve_num:
			doheat(True)
			txt = "heating started due to "
			if grt >= stp.total:
				txt += "sum of valve positions = " + str(grt) 
			else:			
				v = stp.valves[valve_key]
				dn = stp.devices[valve_key][2]
				rn = stp.rooms[str(stp.devices[valve_key][3])][0] 
				txt += "room " + str(rn) + ", device " + str(dn) + " with value " + str(v)
			var.logger.info(txt)
		else:
			var.logger.info("heating stopped.")
			doheat(False)

# check if its right time to update
def rightTime(what):
	tm = time.time()
	if tm > stp.intervals[what][2]:
		stp.intervals[what][2] = tm + stp.intervals[what][0]
		return True
	else:
		return False

#
# beta features
#
def dayMode():
	## day = [0-from_str, 1-to_str, 2-switch%, 3-total or per, 4-mode ("total"/"per"), 5-check interval, 6-valves]
	md = isTime()
	if md != -1:
		if md != var.actDayMode:
			kv = stp.day[md]
			var.actDayMode = md
			var.logger.debug("Switching day mode to " + str(md) + " = " + str(kv))
			stp.valve_switch = kv[2]
			if kv[4] == "total":
				stp.total_switch = kv[3]
			else:
				stp.per_switch = kv[3]
			stp.preference = kv[4]
			stp.intervals["max"][0] = kv[5]
			stp.valve_num = kv[6]
#
# beta
#

def doLoop():
	while 1:
		# do upgrade according schedule
		if rightTime("upg"):
			doUpdate()
		# do update variables according schedule					
		if rightTime("var"):
			updateAllTimes()
			saveBridge()
		# check max according schedule
		if rightTime("max"):
			## beta features here
			if tryRead("beta", "no", False).upper() == "YES":
				dayMode()
			## end of beta			
			cmd = getCMD()
			if cmd == "init":
				closeMAX()
				prepare()
			elif cmd == "mail":
				sendStatus()
			elif cmd == "quit":
				break
			elif cmd == "dump":
				dumpMAX(0)
			elif cmd == "log_debug":
				var.logger.info("Logging level set to DEBUG")
				var.logger.setLevel(logging.DEBUG)
			elif cmd == "log_info":
				var.logger.info("Logging level set to INFO")
				var.logger.setLevel(logging.INFO)
			elif cmd == "ssdebugon":
				stp.globalDebugSS = not stp.globalDebugSS
			elif cmd == "uptime":
				updateUptime()
			elif cmd[0:4] == "mute":
				key = cmd[4:]
				if var.d_W.has_key(key):
					var.d_W[key][0] = datetime.now()
					var.d_W[key][1] = True
					var.logger.debug("OWW for key " + str(key) + " is muted for " + str(stp.intervals["oww"][2]) + " seconds.")
			elif cmd == "rebridge":
				if not loadBridge():
					var.logger.error("Error loading bridge file!")
			elif cmd == "updatetime":
				updateAllTimes()
			elif cmd == "led":
				if var.heating:
					queueMsg("H")
				else:
					queueMsg("S")
			elif cmd == "upgrade":
				doUpdate()			
			readMAXData(False)
			if not var.error:
				getControlValues()
				doControl()
				doDevLogging()

		time.sleep(stp.intervals["slp"][0])

	
def getControlValues():
	# try read preference settings, total or per
	stp.preference = tryRead("pref", "total", True)
	# try read % valve for heat command
	stp.valve_switch = tryRead("valve", 35, True)
	if stp.preference == "per":
		stp.per_switch = tryRead("per", 15, True)
	elif stp.preference == "total":
		stp.total_switch = tryRead("total", 150, True)
	# setup total variable as integer
	stp.total = 100
	# try get readMAX interval value, if not set it
	stp.intervals["max"][0] = tryRead("int", 90, True)
	stp.intervals["slp"][0] = stp.intervals["max"][0] / stp.intervals["slp"][1]
	# try read num of valves to turn heat on
	stp.valve_num = tryRead("valves", 1, True)
	# try read how many minutes you can ignore valve after closing window
	var.ignore_time = tryRead("ign_op", 20, True)
	var.no_oww = tryRead("no_oww", 0, True)

def setupInit():
	# threshold in seconds, so 10 minutes are 10*60 seconds
	# abnormal count of warning is
	stp.abnormalCount = 30
	# interval as "name": [interval=how often is checked, mute interval, next_time]
	tm = time.time()
	stp.intervals ={"max": [90, 0, tm], \
					"upg": [4*60*60, 0, tm], \
					"var": [10*60, 0, tm], \
					# threshold, send every X, muted for X
					"oww": [10*60, 30*60, 45*60], \
					# threshold, muted for X, time.time()
					"wrn": [15*60, 60*60, tm], \
					"err": [0, 0, 0.0], \
					# just sleep value, always calculated as max[0] / slp[1]
					"slp": [30, 3, 0]}
	# day windows/intervals
	## day = [0-from_str, 1-to_str, 2-switch%, 3-total or per, 4-mode ("total"/"per"), 5-check interval, 6-valves]
	stp.day =  [["00:00", "06:00", 40, 185, "total", 240, 2], \
				["06:00", "10:00", 40,  30,   "per", 120, 1], \
				["10:00", "14:00", 40,  30,   "per", 120, 1], \
				["14:00", "22:00", 40,  30,   "per", 120, 1], \
				["22:00", "23:59", 40, 185, "total", 240, 1]]
	
def checkDay():
	if len(stp.day) > 1:
		for i in range(len(stp.day) - 1):
			# time.strptime(k[0], "%H:%M")
			if time.strptime(stp.day[i+1][0], "%H:%M") <= time.strptime(stp.day[i][0], "%H:%M"):
				var.logger.error("Day mode table is wrong! Using default table!")
				stp.day = [["00:00", "23:59", 40, 185, "total", 120, 1]]

def varInit():
	# open window dictionary
	var.d_W = {}
	var.actDayMode = -1
	var.d_ignore = {}
	
def prepare():
	stp.percentage = 3
	stp.github = "https://raw.github.com/autopower/thermeq3/master/"
	stp.homedir = "/root/"
	stp.log_filename = stp.place + stp.devname + ".log"
	startLog()
	stp.appStartTime = time.time()
			
	stp.csv_log = stp.place + stp.devname + ".csv"
	stp.bridgefile = stp.place + stp.devname + ".bridge"
 
	# dictionaries for MAX
	stp.maxid = {"sn":"000000", "rf":"", "fw":""}
	stp.valves = {}
	stp.rooms = {}
	stp.devices = {}
	
	# initialize variables
	setupInit()
	varInit()
	getControlValues()
	
	var.csv = None
	# number of readings when we heating
	var.heatReadings = 0
	
	# clear errors
	var.heating = False
	var.err2Clear = False
	var.err2LastStatus = False
	var.error = False
	# initialize bridge values
	if not loadBridge():
		var.value.put(stp.cw["errs"], "0")
		var.value.put(stp.cw["terrs"], "0")
		var.value.put(stp.cw["ht"], str(var.ht))
		var.value.put(stp.cw["cmd"], "")
	updateStatus("start")
	queueMsg("S")
	
	exportCSV("init")
	readMAXData(True)
	exportCSV("headers")
	updateCounters(False)
	
if __name__ == '__main__':
	stp = setup()
	stp.version = 118
	# turn off writing <funcname> START, <funcname> STOP into the DEBUG, just write DEBUG
	stp.globalDebugSS = False
	stp.cw = {"status":"status", \
		  "int":   "interval", \
		  "ht":    "heattime", \
		  "errs":  "error", \
		  "terrs": "totalerrors", \
		  "valve": "valve_pos", \
		  "cmd":   "command", \
		  "msg":   "msg", \
		  "dump":  "dumpdata", \
		  "uptime":"uptime", \
		  "appuptime":"app_uptime", \
		  "total": "total_switch", \
		  "per":   "per_switch", \
		  "pref":  "preference", \
		  "htstr": "heattime_string", \
		  "valves": "valves", \
		  "daily": "daily", \
		  "beta": "beta", \
		  "ign_op": "ignore_opened", \
		  "no_oww": "no_oww"}
	stp.statMsg = {"idle": "idle", "heat": "heating", "start": "starting", "dead": "dead"}
	
	var = variables()
	# heat times; total: [totalheattime, time.time()]   
	var.ht = {"total": [0, 0.0]}
	var.dev_log = {}
	var.msgQ = []
	
	# initialize bridge
	var.value = BridgeClient()
	
	if path.ismount("/mnt/sda1"):
		stp.place = "/mnt/sda1/"
	elif path.ismount("/mnt/sdb1"):
		stp.place = "/mnt/sdb1/"
	else:
		err_str = "Error: can't find mounted storage device! Please mount SD card or USB key and run program again."
		llError(err_str)
		var.value.put(stp.cw["msg"], "Q")
		exit()
	
	try:
		stp.myip = socket.gethostbyname(socket.gethostname())
	except Exception, e:
		err_str = "Error getting IP address from hostname, please check resolv.conf or hosts or both!\r\n"
		err_str += "Error code: " + str(e) + "\r\n"
		err_str += "Traceback: " + str(traceback.format_exc()) +"\r\n"
		llError(err_str)
		var.value.put(stp.cw["msg"], "Q")
		exit()
	
	execfile("/root/config.py")
		
	stp.stderr_log = stp.place + stp.devname + "_error.log"

	#redir stderr
	redirErr(True)
	
	#sys.excepthook = myHandler
	prepare()
	sendErrorLog()
	
	# this is it
	doLoop()
	
	var.logger.close()
	updateStatus("dead")
	closeMAX()
	exportCSV("close")
	queueMsg("D")
	redirErr(False)
