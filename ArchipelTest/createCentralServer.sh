#!/bin/bash

set +x
set -e

qemu-img create -f qcow2 testfiles/archipel-central-server.qcow2 32G
#chmod 777 testfiles/archipel-central-server.qcow2
virt-install --name "archipel-central-server"\
   --connect qemu:///system  \
   --ram 1024\
   --disk path=./testfiles/archipel-central-server.qcow2,format=qcow2 \
   --location http://mirror.centos.org/centos/6/os/x86_64/ \
   --nographics \
   --noreboot \
   --initrd-inject=testfiles/archipel-central-server.ks \
   --extra-args="ks=file:/archipel-central-server.ks \
      console=tty0 console=ttyS0,115200" \
   --network network=archipel-test-network,mac=52:54:00:00:01:33

mkdir virtimage/
guestmount -a testfiles/archipel-central-server.qcow2 -i virtimage/ ||  echo "ERROR : unable to mount guest fs. libguestfs-tools not installed ?"

# copying extra files to the virt image
cp testfiles/ejabberd.cfg virtimage/etc/ejabberd/ejabberd.cfg.template
cp testfiles/archipel-central-server virtimage/usr/bin/

umount virtimage/
