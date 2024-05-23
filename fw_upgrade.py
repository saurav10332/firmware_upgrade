"""Module to support performing FW upgrades on cable modems.

Supports:

* TG2492-OEM (SNMP)
* CH7465-OEM/OFW (SNMP)
* TG3492 (SNMP)
* F3896 (SNMP)
* F5685LGB (console)

Exported objects:

* (function) console_upgrade(brainbox_ip: str, atom_port: int, target_fw: str) -> bool | None
* (function) snmp_upgrade(cm_ip: str, target_fw: str) -> bool | None
"""
from __future__ import annotations

import logging
import time

from globalfunctions.ap_functions import snmp
from globalfunctions.remoting import telnet
from pysnmp.hlapi import Integer, IpAddress, OctetString

logger = logging.getLogger(__name__)


def console_upgrade(brainbox_ip: str, atom_port: int, target_fw: str) -> bool | None:
    """Perform a firmware change on a cable modem via console commands through a Brainbox/Telnet connection.

    Supports: F5685

    :param brainbox_ip: IP address of Brainbox connected to DUT (eg: 10.55.69.106)
    :param atom_port: Brainbox port number connected to ATOM side of DUT console (eg: 9001)
    :param target_fw: FW filename as it appears in 'peripheral' folder of server
        (eg: F5685_2.40.3a-2210.4_2023-03-24T14-32-30.pkgtb)
    :return: New FW as read from DUT on success, else None
    """
    dut = telnet.TELNET(brainbox_ip, atom_port)
    if not dut.connect():
        logger.error("Unable to open Telnet connection to Brainbox, aborting firmware upgrade...")
        return None

    # Set FW DL Server address
    logger.info("Setting DL server address to 'https://gateway-dl.lab.nl.dmdsdp.com/'")
    command = ("dmcli eRT setv Device.DeviceInfo.X_RDKCENTRAL-COM_FirmwareDownloadURL string "
              "https://gateway-dl.lab.nl.dmdsdp.com/")
    retval = dut.execute(command)
    if retval is None:
        logger.error("Error setting DL server address")
    time.sleep(2)

    # Set FW filename to download
    logger.info("Setting FW filename to %s", target_fw)
    command = f'dmcli eRT setv Device.DeviceInfo.X_RDKCENTRAL-COM_FirmwareToDownload string "{target_fw}"'
    retval = dut.execute(command)
    if retval is None:
        logger.error("Error setting FW filename")
    time.sleep(2)

    # Trigger FW download
    logger.info("Triggering FW download")
    command = "dmcli eRT setv Device.DeviceInfo.X_RDKCENTRAL-COM_FirmwareDownloadNow bool true"
    retval = dut.execute(command)
    if retval is None:
        logger.error("Error triggering FW download")

    # Wait for reboot message in console
    logger.info("Waiting for reboot message in console (max 120s)")
    dut.conn.set_timeout(timeout=120)  # Set max timeout to 120s
    try:
        dut.conn.expect("reboot: Restarting system")  # Wait for reboot message to appear
    except Exception as err:
        # Error waiting for reboot message
        logger.error("No reboot detected, failed to change FW")
        logger.debug("Exception: %s", type(err).__name__)
        return None

    logger.info("DUT reboot initiated, waiting 120s before checks")
    time.sleep(120)

    # Wait for login prompt to appear
    logger.info("Checking if AP online...")
    dut.conn.set_timeout(timeout=5)  # Set timeout to 5s for prompt checks
    while_count = 0  # Set while_count to zero
    while while_count < 4:
        try:
            dut.conn.send("\r")
            dut.conn.expect("login:")
        except Exception as err:
            logger.info("Prompt not detected, retrying in 30s")
            logger.debug("Exception: %s", type(err).__name__)
            time.sleep(30)  # Delay before next check
            while_count += 1  # Increment while counter
        else:
            logger.info("DUT back online")
            break  # Exit loop if prompt seen

    dut.conn.set_timeout(timeout=30)  # Reset timeout to reasonable value
    dut.close()  # Close Telnet connection

    if while_count >= 4:
        logger.error("Prompt not detected after reboot, possible failure while changing FW")
        return None

    logger.info("Upgrade completed")
    return True


def snmp_upgrade(cm_ip: str, target_fw: str) -> bool | None:
    """Perform a firmware change on a cable modem via SNMP.

    Supports: TG2492, CH7465-OEM, CH7465-OFW, TG3492, F3896.

    :param cm_ip: Cable modem IP address of the DUT, obtained via console or CMTS (eg: 10.11.142.122)
    :param target_fw: FW filename as it appears in 'firmware' folder of server (eg: LG-RDK_5.7.36-2210.4_mono_D3_1.p7b)
    :return: True on success, else None
    """
    # Define required OID values
    oid_sys_descr = ".1.3.6.1.2.1.1.1.0"
    oid_http_mode = ".1.3.6.1.2.1.69.1.3.8.0"
    oid_fw_filename = ".1.3.6.1.2.1.69.1.3.2.0"
    oid_dl_server = ".1.3.6.1.2.1.69.1.3.1.0"
    oid_start_dl = ".1.3.6.1.2.1.69.1.3.3.0"
    oid_dl_status = ".1.3.6.1.2.1.69.1.3.4.0"

    target_fw = "firmware/" + target_fw  # Append 'firmware/' directory to start of target_fw
    ip_server = "172.30.144.122"  # Set FW server IP address

    dut_snmp = snmp.SNMP(cm_ip)  # Create instance of SNMP class for DUT

    # Set HTTP mode
    dut_snmp.execute_set(oid_http_mode, Integer(2))
    check = dut_snmp.execute_get(oid_http_mode)
    logger.info("HTTP mode: %s", str(check))

    # Set FW name/path
    dut_snmp.execute_set(oid_fw_filename, OctetString(target_fw))
    check = dut_snmp.execute_get(oid_fw_filename)
    logger.info("FW filepath: %s", str(check))

    # Set download server IP
    dut_snmp.execute_set(oid_dl_server, IpAddress(ip_server))
    check = dut_snmp.execute_get(oid_dl_server)
    logger.info("DL server: %s", str(check))

    # Begin upgrade
    dut_snmp.execute_set(oid_start_dl, Integer(1))
    check = dut_snmp.execute_get(oid_start_dl)
    logger.info("Start DL status: %s", str(check))

    time.sleep(5)  # 5s wait before first check

    status = dut_snmp.execute_get(oid_dl_status)
    if type(status) == Integer:
        status = int(status)
    else:
        logger.error("Invalid upgrade status, exiting...")
        return None
    logger.info("DL status: %s", str(status))

    if status != 1:
        logger.error("Upgrade failed, status: %s", str(status))
        return None

    logger.info("Upgrade started, starting status checks in 60s")
    time.sleep(60)

    # Wait for FW upgrade reboot - SUPPRESS EXECUTE GET ERROR HERE?
    while_counter = 0
    while while_counter < 20 and status == 1:
        new_status = dut_snmp.execute_get(oid_dl_status)
        if new_status is not None and type(new_status) == Integer:
            status = int(new_status)
            logger.info("DUT online, current status: %s (%ss elapsed)", str(status), str(while_counter * 30))
        else:
            logger.info("No response from DUT, rechecking in 30s (%ss elapsed)", str(while_counter * 30))
        time.sleep(30)
        while_counter += 1

    if status != 3:
        logger.error("Upgrade not completed, exiting...")
        return None
    # Wait for possible reboot
    logger.info("Upgrade completed, checking for SNMP response from DUT")
    while_counter = 0
    sys_descr = ""
    while while_counter < 20 and len(sys_descr) == 0:
        sd_value = str(dut_snmp.execute_get(oid_sys_descr))
        if len(sd_value) > 0 and sd_value != "None":
            sys_descr = sd_value
            logger.info("DUT online, sysDescr.0 output: %s", sys_descr)
            return True
        logger.info("No sysDescr.0 output from DUT, rechecking in 30s (%ss elapsed)", str(while_counter * 30))
        time.sleep(30)
        while_counter += 1
    return None
