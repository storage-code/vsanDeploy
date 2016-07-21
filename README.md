This file includes sample code for configure VSAN cluster.

Before running this script, please make sure you have a vc cluster and proper VSAN license.

The code has been tested with the following configuration

Testbed: One cluster with four hosts. Each host has one 50G SSD and two 100G SSD

Preconditions:

1. Please make sure your VSAN's license contains All-Flash if deploying All-Flash
2. The cluster does not have VSAN turned on
3. The hosts are part of a vsphere Cluster

Case1: Create a VSAN cluster 

python vsanDeploy.py -s <VCENTERSERVER> -u user -p password --cluster CLUSTER --allflash --vmknic vmkX --vsanlicense 
