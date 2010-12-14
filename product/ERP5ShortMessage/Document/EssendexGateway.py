# -*- coding: utf-8 -*-
##############################################################################
#                                                                             
# Copyright (c) 2010 Nexedi SA and Contributors. All Rights Reserved.       
#                    Francois-Xavier Algrain <fxalgrain@tiolive.com>                  
#                                                                             
# WARNING: This program as such is intended to be used by professional        
# programmers who take the whole responsability of assessing all potential    
# consequences resulting from its eventual inadequacies and bugs              
# End users who are looking for a ready-to-use solution with commercial       
# garantees and support are strongly adviced to contract a Free Software      
# Service Company                                                             
#                                                                             
# This program is Free Software; you can redistribute it and/or               
# modify it under the terms of the GNU General Public License                 
# as published by the Free Software Foundation; either version 2              
# of the License, or (at your option) any later version.                      
#                                                                             
# This program is distributed in the hope that it will be useful,             
# but WITHOUT ANY WARRANTY; without even the implied warranty of              
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the               
# GNU General Public License for more details.                                
#                                                                             
# You should have received a copy of the GNU General Public License           
# along with this program; if not, write to the Free Software                 
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA. 
#                                                                             
##############################################################################
"""Receive or send SMS"""

#Import python module
import urllib
from lxml import etree
from DateTime import DateTime

#Import Zope module
from AccessControl import ClassSecurityInfo, \
                          Unauthorized 
from AccessControl.SecurityManagement import  getSecurityManager, \
                                              setSecurityManager, \
                                              newSecurityManager 
import zope.interface
from zLOG import LOG, INFO

from Products.ERP5Type import Permissions, PropertySheet, interfaces
from Products.ERP5Type.XMLObject import XMLObject
from Products.ERP5Security.ERP5UserManager import SUPER_USER 

#Product Module
from Products.ERP5ShortMessage.Errors import SMSGatewayError



class EssendexGateway(XMLObject):

    """Base of SMS an Gateway. You can use push notification for delivered and new message notification."""
    meta_type='Essendex Gateway'
    portal_type = 'Essendex Gateway'
    security = ClassSecurityInfo()


    add_permission = Permissions.AddPortalContent

    zope.interface.implements(interfaces.ISmsGateway)

    # Declarative security
    security = ClassSecurityInfo()
    security.declareObjectProtected(Permissions.AccessContentsInformation)

    # Declarative properties
    property_sheets = ( PropertySheet.Base
                      , PropertySheet.XMLObject
                      , PropertySheet.Reference
                      , PropertySheet.SMSGateway
                      )

    api_url = "https://www.esendex.com/secure/messenger/formpost"
    security.declarePublic('getAllowedMessageType')
    def getAllowedMessageType(self):
      """List of all message type"""
      return ['text', 'binary', 'smartMessage', 'unicode']
      
    security.declarePrivate("_fetchPageAsDict")
    def _fetchPageAsDict(self,page):
      """Page result is like Key=value in text format. 
         We transform it to a more powerfull dictionnary"""
      result = dict()
      for line in page.readlines():
        parts = line.split('=')
        #Remove \n et \r from value
        result[parts[0]] = urllib.unquote(parts[1].replace('\r','').replace('\n',''))

      return result
      
    security.declarePrivate("_transformPhoneUrlToGatewayNumber")
    def _transformPhoneUrlToGatewayNumber(self,phone):
      """Transform url of phone number to a valid phone number (gateway side)"""
      return phone.replace('tel:', '').replace('+','')

    security.declareProtected(Permissions.ManagePortal, 'send')
    def send(self, text,recipient,sender=None, message_type="text",test=False,**kw):
      """Send a message.
         Return message id (or list if multiple recipient)
         Kw Parameters:
         validity_period -- Validity Period of SMS (default,0)
         """

      if message_type not in self.getAllowedMessageType():
        raise ValueError, "Type of message in not allowed"

      validity_period = kw.get('validity_period',0)
      
      if not isinstance(recipient, str):
        recipient = ",".join([self._transformPhoneUrlToGatewayNumber(x) for x in recipient])
      else:
        recipient = self._transformPhoneUrlToGatewayNumber(recipient)

      base_url = self.api_url + "/SendSMS.aspx"
      params = {'Username': self.getGatewayUser(),
                'Password': self.getGatewayPassword(),
                'Account': self.getGatewayAccount(),
                'Recipient': recipient,
                'Body': text,
                'Type': message_type.capitalize(),
                'ValidityPeriod': validity_period,
                'PlainText': 1,
                }
      
      if sender:
        params['Originator'] = self._transformPhoneUrlToGatewayNumber(sender)
      else:
        params['Originator'] = self.getDefaultSender()
      if test or self.isSimulationMode():
        params['Test'] = 1
      
      params = urllib.urlencode(params)
      page = urllib.urlopen(base_url, params)
      result = self._fetchPageAsDict(page)
      if result['Result'] == "OK":
        message_ids = result.get('MessageIDs', "")
        #If a message is sent to multiple recipients, multiple IDs are returned
        #each seperated by a comma.
        return message_ids.split(",")
      elif result['Result'] == "Error":
        #we get an error when call the gateway
        raise SMSGatewayError, urllib.unquote(result.get('Message', "Impossible to send the SMS"))
      elif result['Result'] == "Test":
        #just a test, no message id
        return None
      else:
        LOG("Unknow SMS gateway result",INFO, result)
        return None

    security.declareProtected(Permissions.ManagePortal, 'getMessageStatus')
    def getMessageStatus(self, message_id):
      """Retrive the status of a message"""
      base_url = self.api_url + "/QueryStatus.aspx"

      params = {'Username': self.getGatewayUser(),
                'Password': self.getGatewayPassword(),
                'Account': self.getGatewayAccount(),
                'PlainText': 1,
                'MessageID': message_id,
                }

      params = urllib.urlencode(params)
      page = urllib.urlopen(base_url, params)
      result = self._fetchPageAsDict(page)

      if result['Result'] == "OK":
        return result.get('MessageStatus').lower()
      elif result['Result'] == "Error":
        #we get an error when call the gateway
        raise SMSGatewayError, urllib.unquote(result.get('Message', "Impossible to get the message status"))

    security.declarePublic('receive')
    def receive(self,REQUEST):
      """Receive push notification"""
      
      #XML is stored is BODY of request
      datas = REQUEST['BODY']
      
      if not datas:
        raise SMSGatewayError, "Impossible to notify nothing"
      
      #Get current user
      sm = getSecurityManager()
      try:
        #Use SUPER_USER
        portal_membership = self.getPortalObject().portal_membership
        newSecurityManager(None, portal_membership.getMemberById(SUPER_USER)) 
        
        #Parse XML 
        root = etree.fromstring(datas)
        
        #Choice action corresponding to the notification type
        notification_type =  root.tag
        
        #Parse text XML Element to dict
        xml = {}
        for child in root.getchildren():
          xml[child.tag] = child.text   
          
        #Check Account id
        if xml['AccountId'] != self.getGatewayAccountId():
          raise Unauthorized, 'Bad accound id (%s)' % xml['AccountId']
        
        if notification_type == 'InboundMessage':
          self.notifyReception(xml)
        elif notification_type == 'MessageDelivered':
          self.notifyDelivery(xml)
        elif notification_type == 'MessageError':
          raise SMSGatewayError, "'MessageError' notification is not implemented (%s)" % str(kw)
        elif notification_type == 'SubscriptionEvent':
          raise SMSGatewayError, "'MessageError' notification is not implemented (%s)" % str(kw)
        else:
          raise SMSGatewayError, "Unknow '%s' notification (%s)" % (notification_type, str(kw))
      finally:
        #Restore orinal user
        setSecurityManager(sm)
      
      
    security.declareProtected(Permissions.ManagePortal, 'notifyReception')
    def notifyReception(self, xml):
      """The gateway inform what we ha a new message.
         root: lxml Element"""

      """
      <InboundMessage>
        <Id>{guid-of-push-notification}</Id>
        <MessageId>{guid-of-inbound-message}</MessageId>
        <AccountId>{guid-of-esendex-account-for-message}</AccountId>
        <MessageText>{Message text of inbound message}</MessageText>
        <From>{phone number of sender of the message}</From>
        <To>{phone number of the recipient of the inbound message (the
        virtual number of the esendex account in use)}</To>
      </InboundMessage>
      """   

      #Convert phone as erp5 compliant
      def parsePhoneNumber(number):
        #XXX: Should register well formatted number or brut number ?
        #return number
        return "+%s(%s)-%s" % (number[0:2],0,number[2:])
        

      #Create the new sms in activities
      module = self.getDefaultModule("Short Message")
      module.activate(activity='SQLQueue').EventModule_pushNewSMS(
                              message_id=xml['MessageId'],
                              sender=parsePhoneNumber(xml['From']),
                              recipient=parsePhoneNumber(xml['To']),
                              text_content=xml['MessageText'],
                              message_type='text/plain',
                              reception_date=DateTime())

    security.declareProtected(Permissions.ManagePortal, 'notifyDelivery')
    def notifyDelivery(self, xml):
      """Handle delivery info
        root: lxml Element"""
      """      
      <MessageDelivered>
        <Id>{guid-of-push-notification}</Id>
        <MessageId>{guid-of-inbound-message}</MessageId>
        <AccountId>{guid-of-esendex-account-for-message}</AccountId>
        <OccurredAt>{the UTC DateTime (yyyy-MM-ddThh:mm:ss) that the
        message was delivered to the recipient}</OccurredAt>
      </MessageDelivered>

      """  

      #Convert date to DateTime      
      xml['OccurredAt'] = DateTime(xml['OccurredAt'][0:19])
      
      module = self.getDefaultModule("Short Message")
      module.activate(activity='SQLQueue').EventModule_setEventDelivery(
                            portal_type="Short Message",
                            destination_reference=xml['MessageId'],
                            delivery_date=xml['OccurredAt'])
