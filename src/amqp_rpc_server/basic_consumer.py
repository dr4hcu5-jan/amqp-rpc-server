"""The basic consumer which consumes messages and relays them to an executor function"""
import json
import logging
import secrets
import sys
from typing import Optional, Callable

import pika
import pika.channel
import pika.exceptions
import pika.exchange_type
import pika.frame


class BasicConsumer:
    """The basic consumer handling the connection to the message broker and the running of the
    executor"""
    
    def __init__(
            self,
            amqp_dsn: str,
            exchange_name: str,
            executor: Callable[[bytes], bytes],
            content_validator: Optional[Callable[[bytes], bool]] = None,
            queue_name: str = secrets.token_urlsafe(nbytes=32),
            exchange_type: pika.exchange_type.ExchangeType = pika.exchange_type.ExchangeType.fanout
    ):
        """
        Initialize a new BasicConsumer

        This consumer will handle the connection to the message broker and will handle incoming
        and outgoing messages.

        :param amqp_dsn: The Data Source Name pointing to a AMQPv0-9-1 enabled message broker
        :type amqp_dsn: str
        :param exchange_name: The name of the exchange which this consumer will bind on and
            listen for new incoming messages. If this exchange does not exist, the exchange will
            be created as a fanout exchange allowing multiple consumers reading all messages
        :type exchange_name: str
        :param executor: The function which shall be called if a new message was received and
            acknowledged
        :type executor: Callable[[bytes], bytes]
        :param content_validator: A callable which returns if the content of the message is
            valid for the executor. If the callable returns `False` the message will be rejected.
            If no content_validator is supplied the message will always be acknowledged
        :type content_validator: Callable[[bytes], bool], optional
        :param queue_name: The name of the queue which shall be bound to the exchange by this
            consumer. If no queue name is supplied a name will be autogenerated
        :type queue_name: str, optional
        :param exchange_type: The type of the exchange which is used if the exchange does not
            exist. If the exchange already exists, the exchange_type needs to match the one which
            has already been declared
        :type exchange_type: pika.exchange_type.ExchangeType, optional
        :type queue_name: str, optional
        """
        # Check if the AMQP Data Source Name is not None or emtpy
        if amqp_dsn is None:
            raise ValueError('The amqp_dsn is a required parameter may not be None')
        if amqp_dsn.strip() == '':
            raise ValueError('The amqp_dsn is a required parameter and may not be empty')
        # Check if the exchange name is None or empty
        if exchange_name is None:
            raise ValueError('The exchange_name is a required parameter and may not be None')
        if exchange_name.strip() == '':
            raise ValueError('The exchange_name is a required parameter and may not be empty')
        # Check if the executor is set correctly
        if executor is None:
            raise ValueError('The executor is a required parameter and may not be None')
        # Store the properties to the object
        self._amqp_dsn = amqp_dsn
        self._exchange_name = exchange_name
        self._exchange_type = exchange_type
        self._queue_name = queue_name
        self._executor = executor
        self._content_validator = content_validator
        # Create a logger for the consumer
        self._logger = logging.getLogger('amqp_rpc_server.basic_consumer.BasicConsumer')
        # Initialize some attributes which are needed later and apply typing to them
        self._connection: Optional[pika.SelectConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._qos_prefetch_count = 1
        self._consumer_tag = None
        self._is_consuming = False
        self._is_closing = False
        self.may_reconnect = False
    
    def start(self):
        """Start the consumer by connecting to the message broker"""
        self._connection = self._connect()
        self._connection.ioloop.start()
    
    def stop(self):
        """Stop the consumer and shutdown the connection to the message broker cleanly"""
        if not self._is_closing:
            self._is_closing = True
            self._logger.info('The consumer is stopping and closing the connection to the message '
                              'broker')
            if self._is_consuming:
                self._logger.debug('Stopping the consumer')
                self._stop_consuming()
                # Try to start the ioloop if necessary
                try:
                    self._logger.debug('Restarting the ioloop')
                    self._connection.ioloop.start()
                except Exception as error:  # pylint: disable=broad-except
                    self._logger.debug('IOLoop restart not necessary')
                    pass
            else:
                self._logger.debug('Currently not consuming')
                try:
                    self._connection.ioloop.stop()
                except Exception:  # pylint: disable=broad-except
                    pass
            self._logger.info('Stopped the consumer and closed the connection to the message '
                              'broker')
    
    def _stop_consuming(self):
        """Stop the consumption of messages"""
        if self._channel:
            self._logger.debug('Cancelling the active channel to the message broker')
            self._channel.basic_cancel(self._consumer_tag, self._cb_channel_cancelled)
    
    def _cb_channel_cancelled(self, method_frame: pika.frame.Method):
        """
        Callback invoked if a channel has successfully been cancelled

        :param method_frame: The result of the execution
        :type method_frame: pika.frame.Method
        """
        self._logger.debug('Successfully cancelled the channel at the message broker')
        self._close_channel()
    
    def _close_channel(self):
        """Close the currently active channel"""
        self._logger.debug('Closing the currently active channel to the message broker')
        self._channel.close()
    
    def _connect(self) -> pika.SelectConnection:
        """Connect to the message broker

        :return: The opened connection to the message broker
        :rtype: pika.SelectConnection
        """
        self._logger.info('Connecting to the message broker...')
        self._logger.debug('Connection DSN: %s',
                           self._amqp_dsn)
        # Build the connection parameters
        connection_parameters = pika.URLParameters(self._amqp_dsn)
        # Set the client properties
        connection_parameters.client_properties = {
            'connection_name': secrets.token_urlsafe(nbytes=16),
            'product':         'AMQP-RPC Server',
            'platform':        f'Python {sys.version}',
            'information':     'Licensed under the 3-Clause BSD License. See the LICENSE file '
                               'supplied with this library',
            'copyright':       'Copyright (c) Jan Eike Suchard'
        }
        return pika.SelectConnection(
            parameters=connection_parameters,
            on_open_callback=self._cb_connection_opened,
            on_open_error_callback=self._cb_connection_open_failed,
            on_close_callback=self._cb_connection_closed
        )
    
    def _cb_connection_open_failed(self, connection: pika.BaseConnection, reason: Exception):
        """
        Callback for a failed connection attempt to a message broker

        :param connection: The connection that failed
        :type connection: pika.BaseConnection, unused
        :param reason: The reason for the connection failure
        :type reason: Exception
        """
        self._logger.critical('Failed to establish a connection to the message broker: %s',
                              reason)
        self.may_reconnect = True
        self.stop()
        return
    
    def _cb_connection_closed(self, connection: pika.connection.Connection, reason: Exception):
        """
        Callback for an unexpected connection closure

        :param connection: The connection that has been closed
        :type connection: pika.connection.Connection
        :param reason: The reason for the connection closure
        :type reason: Exception
        """
        # Unset the channel so no more messages can be sent
        self._channel = None
        if self._is_closing:
            self._connection.ioloop.stop()
        else:
            self._logger.error('The connection to the message broker was closed unexpectedly for '
                               'the following reason: %s',
                               reason)
            self.may_reconnect = True
    
    def _cb_connection_opened(self, connection: pika.BaseConnection):
        """Handle an opened connection

        :param connection: The connection that has been opened
        :type connection: pika.SelectConnection
        """
        self._logger.debug('Connected to the message broker')
        self._logger.debug('Server properties: %s',
                           connection.params.client_properties)
        # Call for opening a channel
        self._open_channel()
    
    def _open_channel(self):
        """Try to open a new channel to the message broker"""
        self._logger.debug('Opening a new channel between the message broker and the server...')
        self._connection.channel(on_open_callback=self._cb_channel_opened)
    
    def _cb_channel_opened(self, channel: pika.channel.Channel):
        self._logger.debug('Opened a channel between the message broker and the server')
        self._logger.debug('Channel number: %s',
                           channel.channel_number)
        # Save the opened channel to the consumer
        self._channel = channel
        # Add a callback for a closed channel
        self._channel.add_on_close_callback(self._cb_channel_closed)
        # Set up the exchange
        self._setup_exchange()
    
    def _cb_channel_closed(self, _channel: pika.channel.Channel, reason: Exception):
        """Callback for how to handle a closed channel"""
        if isinstance(reason, pika.exceptions.ChannelClosedByBroker):
            self._logger.critical('The message broker closed the currently active channel')
            self.may_reconnect = True
            self._is_closing = True
            self._close_connection()
        elif isinstance(reason, pika.exceptions.ChannelClosedByClient):
            self._logger.info('The server closed the connection to the message broker')
            self._close_connection()
        else:
            self._logger.critical('The channel was closed for an not handled error: %s',
                                  reason)
            self._is_closing = True
            self.may_reconnect = True
            self._close_connection()
    
    def _setup_exchange(self):
        """Set up the binding of the exchange and the possible creation of the exchange"""
        self._logger.debug('Declaring an exchange on the message broker...')
        self._logger.debug('Exchange Name: %s',
                           self._exchange_name)
        self._channel.exchange_declare(
            exchange=self._exchange_name,
            exchange_type=self._exchange_type.value,
            callback=self._cb_exchange_declared
        )
    
    def _cb_exchange_declared(self, method_frame: pika.frame.Method):
        """
        Handle a successfully declared exchange

        This callback will initiate the setup of a queue used to read messages from the message
        broker

        :param method_frame: Status of the exchange declaration
        :type method_frame: pika.frame.Method, unused
        :return:
        """
        self._logger.debug('Successfully declared an exchange on the message broker')
        self._logger.debug('Method Frame Contents: %s',
                           method_frame)
        self._setup_queue()
    
    def _setup_queue(self):
        """Set up a queue which is attached to the exchange"""
        self._logger.debug('Setting up a queue at the message broker...')
        self._channel.queue_declare(
            self._queue_name,
            passive=False,
            exclusive=False,
            auto_delete=False,
            durable=True,
            callback=self._cb_queue_declared
        )
    
    def _cb_queue_declared(self, method_frame: pika.frame.Method):
        """
        Callback for a successfully created queue

        This callback will initiate the binding of the queue to the exchange

        :param method_frame: Status of the Method
        :type method_frame: pika.frame.Method, unused
        """
        self._logger.debug('Successfully set up a queue at the message broker')
        self._logger.debug('Method Frame Contents: %s',
                           method_frame)
        self._logger.debug('Binding the queue to the specified/created exchange...')
        # Currently, there will be no routing key set since I'm not sure on how I want to
        # implement the different exchange types
        self._channel.queue_bind(
            queue=self._queue_name,
            exchange=self._exchange_name,
            callback=self._cb_queue_bound
        )
    
    def _cb_queue_bound(self, method_frame: pika.frame.Method):
        """
        Callback for a successful execution of the queue binding

        This will trigger a setup for the quality of service on this consumer

        :param method_frame: The result of the execution
        :type method_frame: pika.frame.Method
        """
        self._logger.debug('Successfully bound the queue to the exchange')
        self._logger.debug('Method Frame Contents: %s',
                           method_frame)
        self._logger.debug('Setting the Quality of service for this consumer')
        self._channel.basic_qos(
            prefetch_count=self._qos_prefetch_count,
            callback=self._cb_channel_qos_set
        )
    
    def _cb_channel_qos_set(self, method_frame: pika.frame.Method):
        """
        Callback for a successful execution of the Basic.Qos command on the message broker

        Since all setup processes are now finished we will start the consummation of messages

        :param method_frame: The result of the execution
        :type method_frame: pika.frame.Method, unused
        """
        self._logger.debug('Successfully set the quality of service values')
        self._logger.debug('Method frame contents: %s',
                           method_frame)
        self._start_message_consuming()
    
    def _start_message_consuming(self):
        """
        This call will start consuming messages from the message broker.
        """
        self._logger.info('Connection successfully established and configured')
        self._logger.info('Enabling the message consumption')
        self._channel.add_on_cancel_callback(self._cb_consumer_cancelled)
        self._is_consuming = True
        self._consumer_tag = self._channel.basic_consume(
            self._queue_name,
            on_message_callback=self._cb_new_message_received,
            exclusive=False,
            auto_ack=False
        )
    
    def _cb_consumer_cancelled(self, method_frame: pika.frame.Method):
        """
        Callback invoked if a consumer is cancelled by the message broker

        :param method_frame: The Basic.Cancel frame from the cancellation
        :type method_frame: pika.frame.Method
        :return:
        """
        self._logger.critical('The consumer was cancelled by the message broker: %s',
                              method_frame)
        if self._channel:
            self._channel.close()
    
    def _cb_new_message_received(
            self,
            channel: pika.channel.Channel,
            delivery_properties: pika.spec.Basic.Deliver,
            message_properties: pika.spec.BasicProperties,
            message_body: bytes
    ):
        """
        Callback for when a new message is received.

        If a new message is received the contents of message will be passed through the validator
        (if a validator was supplied). If the validator returns `True` the message will be
        acknowledged and passed to the executor. The result of the executor will be returned via
        the incoming channel to the sender. The incoming message therefore needs the following
        properties: `correlation_id`, `reply-to`

        :param channel: The channel over which the message was received
        :type channel: pika.channel.Channel
        :param delivery_properties: The properties of the delivery
        :type delivery_properties: pika.spec.Basic.Deliver
        :param message_properties: The properties of the message
        :type message_properties: pika.spec.BasicProperties
        :param message_body: The content of the message
        :type message_body: bytes
        """
        # Try to extract an app_id from the message properties for more accurate logging
        _sender_id = 'unknown' if message_properties.app_id is None else message_properties.app_id
        self._logger.info('%s - %s - Received new message from the message broker by sent by: %s',
                          _sender_id, delivery_properties.delivery_tag, _sender_id)
        # Check the message properties for a correlation id and the reply-to field
        if None in [message_properties.correlation_id, message_properties.reply_to]:
            self._logger.warning('%s - %s - The message did not contain the needed properties. '
                                 'This message will be rejected',
                                 _sender_id, delivery_properties.delivery_tag)
            # Reject the message
            channel.basic_reject(delivery_properties.delivery_tag, requeue=False)
            return
        # Since the required properties were found the message will now be passed to the
        # validator, if a validator was supplied
        message_valid = True
        if self._content_validator is not None:
            message_valid = self._content_validator(message_body)
        if not message_valid:
            self._logger.warning('%s - %s - The message was deemed invalid by the validator. The '
                                 'message will be rejected and the sender will be informed',
                                 _sender_id, delivery_properties.delivery_tag)
            # Reject
            channel.basic_reject(delivery_properties.delivery_tag, requeue=False)
            # Build the information for the sender
            _invalid_message_notification = {
                "error": "invalid_message_content"
            }
            # Send a message back to the sender
            channel.basic_publish(
                exchange='',
                routing_key=message_properties.reply_to,
                body=json.dumps(_invalid_message_notification).encode('utf-8'),
                properties=pika.BasicProperties(
                    correlation_id=message_properties.correlation_id,
                    content_encoding='utf-8'
                )
            )
            return

        # Now run the executor and get its results and catch all errors happening which are not
        # explicitly caught during the execution
        try:
            # Since the message is valid it now will be acknowledged
            channel.basic_ack(delivery_properties.delivery_tag)
            results = self._executor(message_body)
        except Exception as error:  # pylint: disable=broad-except
            # Since an error occurred we now put the information from the error into the response
            exception_data = {
                "error": str(error)
            }
            results = json.dumps(exception_data, ensure_ascii=False).encode('utf-8')
        # Send the response to the message broker
        channel.basic_publish(
            exchange='',
            routing_key=message_properties.reply_to,
            body=results,
            properties=pika.BasicProperties(
                correlation_id=message_properties.correlation_id,
                content_encoding='utf-8'
            )
        )
        return

    def _close_connection(self):
        self._consuming = False
        if self._connection.is_closing or self._connection.is_closed:
            self._logger.info('Connection is closing or already closed')
        else:
            self._logger.info('Closing connection')
            self._connection.close()
