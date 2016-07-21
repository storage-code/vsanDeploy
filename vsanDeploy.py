#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Copyright 2016 VMware, Inc.  All rights reserved.

This file includes sample code for configure VSAN cluster.

Before running this script, please make sure you have a vc cluster and proper VSAN license

The code has been tested with the following configuration
Testbed: One cluster with four hosts. Each host has one 50G SSD and two 100G SSD
Preconditions:
1. Please make sure your VSAN's license contains All-Flash if deploying All-Flash
2. The cluster does not have VSAN turned on
3. The hosts are part of a vsphere Cluster

Case1: Create a VSAN cluster 
python vsanDeploy.py -s <VCENTERSERVER> -u user -p password --cluster CLUSTER --allflash --vmknic vmkX --vsanlicense 

"""

__author__ = 'VMware, Inc'

from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import sys
import ssl
import atexit
import argparse
import getpass
# import the VSAN API python bindings
import vsanmgmtObjects
import vsanapiutils
from operator import itemgetter, attrgetter

def GetArgs():
   """
   Supports the command-line arguments listed below.
   """
   parser = argparse.ArgumentParser(
      description='Process args for VSAN SDK sample application')
   parser.add_argument('-s', '--host', required=True, action='store',
                       help='Remote host to connect to')
   parser.add_argument('-o', '--port', type=int, default=443, action='store',
                       help='Port to connect on')
   parser.add_argument('-u', '--user', required=True, action='store',
                       help='User name to use when connecting to host')
   parser.add_argument('-p', '--password', required=False, action='store',
                       help='Password to use when connecting to host')
   parser.add_argument('--cluster', dest='clusterName', metavar="CLUSTER",
                       default='VSAN-Cluster')
   parser.add_argument('--allflash', action='store_true')
   parser.add_argument('--faultdomains', action='store',
                       help='VSAN fault domains [name:host1,host2 name:host3,host4...]')
   parser.add_argument('--vmknic', action='store', default='vmk0',
                       help='Enable VSAN on the vmknic ')
   parser.add_argument('--vsanlicense', action='store',
                       help='VSAN license key')

   args = parser.parse_args()
   return args


def getClusterInstance(clusterName, serviceInstance):
   content = serviceInstance.RetrieveContent()
   searchIndex = content.searchIndex
   datacenters = content.rootFolder.childEntity
   for datacenter in datacenters:
      cluster = searchIndex.FindChild(datacenter.hostFolder, clusterName)
      if cluster is not None:
         return cluster
   return None

def yes(ques) :
   "Force the user to answer 'yes' or 'no' or something similar. Yes returns true"
   while 1 :
      ans = raw_input(ques)
      ans = str.lower(ans[0:1])
      return True if ans == 'y' else False

def CollectMultiple(content, objects, parameters, handleNotFound=True):
   if len(objects) == 0:
      return {}
   result = None
   pc = content.propertyCollector
   propSet = [vim.PropertySpec(
      type=objects[0].__class__,
      pathSet=parameters
   )]

   while result == None and len(objects) > 0:
      try:
         objectSet = []
         for obj in objects:
            objectSet.append(vim.ObjectSpec(obj=obj))
         specSet = [vim.PropertyFilterSpec(objectSet=objectSet, propSet=propSet)]
         result = pc.RetrieveProperties(specSet=specSet)
      except vim.ManagedObjectNotFound as ex:
         objects.remove(ex.obj)
         result = None

   out = {}
   for x in result:
      out[x.obj] = {}
      for y in x.propSet:
         out[x.obj][y.name] = y.val
   return out


def sizeof_fmt(num, suffix='B'):
   for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
      if abs(num) < 1024.0:
         return "%3.1f%s%s" % (num, unit, suffix)
      num /= 1024.0
   return "%.1f%s%s" % (num, 'Yi', suffix)

# Start program
def main():
   args = GetArgs()
   if args.password:
      password = args.password
   else:
      password = getpass.getpass(prompt='Enter password for host %s and '
                                        'user %s: ' % (args.host, args.user))

   # For python 2.7.9 and later, the defaul SSL conext has more strict
   # connection handshaking rule. We may need turn of the hostname checking
   # and client side cert verification
   context = None
   if sys.version_info[:3] > (2, 7, 8):
      context = ssl.create_default_context()
      context.check_hostname = False
      context.verify_mode = ssl.CERT_NONE

   si = SmartConnect(host=args.host,
                     user=args.user,
                     pwd=password,
                     port=int(args.port),
                     sslContext=context)

   atexit.register(Disconnect, si)

   cluster = getClusterInstance(args.clusterName, si)

   if args.vsanlicense:
      print 'Assign VSAN license'
      lm = si.content.licenseManager
      lam = lm.licenseAssignmentManager
      lam.UpdateAssignedLicense(
         entity=cluster._moId,
         licenseKey=args.vsanlicense
      )

   vcMos = vsanapiutils.GetVsanVcMos(si._stub, context=context)

   vsanClusterSystem = vcMos['vsan-cluster-config-system']
   vsanVcDiskManagementSystem = vcMos['vsan-disk-management-system']

   isallFlash = args.allflash

   print 'Enable VSAN with {} mode'.format('all flash ' if isallFlash else 'hybrid')

   hostProps = CollectMultiple(si.content, cluster.host,
                                    ['name', 'configManager.vsanSystem', 'configManager.storageSystem'])
   hosts = hostProps.keys()

   for host in hosts:
      disks = [result.disk for result in
               hostProps[host]['configManager.vsanSystem'].QueryDisksForVsan() if result.state == 'ineligible']
      print 'Find ineligible disks {} in host {}'.format([disk.displayName for disk in disks], hostProps[host]['name'])
      for disk in disks:
         if yes('Do you want to wipe disk {}?\nPlease Always check the partition table and the data stored'
                 ' on those disks before doing any wipe! (yes/no)?'.format(disk.displayName)):
            hostProps[host]['configManager.storageSystem'].UpdateDiskPartitions(disk.deviceName,
            vim.HostDiskPartitionSpec())

   tasks = []

   configInfo = vim.VsanHostConfigInfo(
      networkInfo=vim.VsanHostConfigInfoNetworkInfo(
         port=[vim.VsanHostConfigInfoNetworkInfoPortConfig(
            device=args.vmknic,
            ipConfig=vim.VsanHostIpConfig(
               upstreamIpAddress='224.1.2.3',
               downstreamIpAddress='224.2.3.4'
            )
         )]
      )
   )

   for host in hosts:
      print 'Enable VSAN trafic in host {} with {}'.format(hostProps[host]['name'], args.vmknic)
      task = hostProps[host]['configManager.vsanSystem'].UpdateVsan_Task(configInfo)
      tasks.append(task)
   vsanapiutils.WaitForTasks(tasks, si)
   del tasks[:]

   print 'Enable VSAN by claiming disks manually'
   #Build vsanReconfigSpec step by step, it only take effect after method VsanClusterReconfig is called
   vsanReconfigSpec = vim.VimVsanReconfigSpec(
      modify=True,
      vsanClusterConfig=vim.VsanClusterConfigInfo(
         enabled=True,
         defaultConfig=vim.VsanClusterConfigInfoHostDefaultInfo(
            autoClaimStorage=False
         )
      )
   )

   if isallFlash:
      print 'Enable deduplication and compression for VSAN'
      vsanReconfigSpec.dataEfficiencyConfig = vim.VsanDataEfficiencyConfig(
         compressionEnabled=True,
         dedupEnabled=True
      )

   if args.faultdomains:
      print 'Add fault domains in vsan'
      faultDomains = []
      #args.faultdomains is a string like f1:host1,host2 f2:host3,host4
      for faultdomain in args.faultdomains.split():
         fname, hostnames = faultdomain.split(':')
         domainSpec = vim.cluster.VsanFaultDomainSpec(
            name=fname,
            hosts=[host for host in hosts
                   if hostProps[host]['name'] in hostnames.split(',')]
         )
         faultDomains.append(domainSpec)

      vsanReconfigSpec.faultDomainsSpec = vim.VimClusterVsanFaultDomainsConfigSpec(
         faultDomains=faultDomains
      )

   task = vsanClusterSystem.VsanClusterReconfig(cluster, vsanReconfigSpec)
   vsanapiutils.WaitForTasks([task], si)

   diskmap = {host: {'cache':[],'capacity':[]} for host in hosts}
   cacheDisks = []
   capacityDisks = []

   if isallFlash:
      #Get eligible ssd from host
      for host in hosts:
         ssds = [result.disk for result in hostProps[host]['configManager.vsanSystem'].QueryDisksForVsan() if
               result.state == 'eligible' and result.disk.ssd]
         smallerSize = min([disk.capacity.block * disk.capacity.blockSize for disk in ssds])
         for ssd in ssds:
            size = ssd.capacity.block * ssd.capacity.blockSize
            if size == smallerSize:
               diskmap[host]['cache'].append(ssd)
               cacheDisks.append((ssd.displayName, sizeof_fmt(size), hostProps[host]['name']))
            else:
               diskmap[host]['capacity'].append(ssd)
               capacityDisks.append((ssd.displayName, sizeof_fmt(size), hostProps[host]['name']))
   else:
      for host in hosts:
         disks = [result.disk for result in hostProps[host]['configManager.vsanSystem'].QueryDisksForVsan() if
               result.state == 'eligible']
         ssds = [disk for disk in disks if disk.ssd]
         hdds = [disk for disk in disks if not disk.ssd]

         for disk in ssds:
            diskmap[host]['cache'].append(disk)
            size = disk.capacity.block * disk.capacity.blockSize
            cacheDisks.append((disk.displayName, sizeof_fmt(size), hostProps[host]['name']))
         for disk in hdds:
            diskmap[host]['capacity'].append(disk)
            size = disk.capacity.block * disk.capacity.blockSize
            capacityDisks.append((disk.displayName, sizeof_fmt(size), hostProps[host]['name']))

   print 'Claim these disks to cache disks'
   for disk in cacheDisks:
      print 'Name:{}, Size:{}, Host:{}'.format(disk[0], disk[1], disk[2])

   print 'Claim these disks to capacity disks'
   for disk in capacityDisks:
      print 'Name:{}, Size:{}, Host:{}'.format(disk[0], disk[1], disk[2])

   for host,disks in diskmap.iteritems():
      if disks['cache'] and disks['capacity']:
         dm = vim.VimVsanHostDiskMappingCreationSpec(
               cacheDisks=disks['cache'],
               capacityDisks=disks['capacity'],
               creationType='allFlash' if isallFlash else 'hybrid',
               host=host
            )

         task = vsanVcDiskManagementSystem.InitializeDiskMappings(dm)
         tasks.append(task)

   print 'Wait for create disk group tasks finish'
   vsanapiutils.WaitForTasks(tasks, si)
   del tasks[:]

   print 'Display disk groups in each host'
   for host in hosts:
      diskMaps = vsanVcDiskManagementSystem.QueryDiskMappings(host)

      for index, diskMap in enumerate(diskMaps, 1):
         print 'Host:{}, DiskGroup:{}, Cache Disks:{}, Capacity Disks:{}'.format(hostProps[host]['name'], index,
                                                                                 diskMap.mapping.ssd.displayName,
                                                                                 [disk.displayName for disk in
                                                                                  diskMap.mapping.nonSsd])

   print 'Enable perf service on this cluster'
   vsanPerfSystem = vcMos['vsan-performance-manager']
   task = vsanPerfSystem.CreateStatsObjectTask(cluster)
   vsanapiutils.WaitForTasks([task], si)

# Start program
if __name__ == "__main__":
   main()
