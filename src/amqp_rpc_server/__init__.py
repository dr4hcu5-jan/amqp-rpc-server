import logging
import secrets
import threading
import time
import typing
import inspect

import pika.exchange_type

from .basic_consumer import BasicConsumer as _BasicConsumer
from .exceptions import MaxConnectionAttemptsReached as _MaxConnectionAttemptsReached

_logger = logging.getLogger(__name__)


class Server:
    
    def __init__(
            self,
            amqp_dsn: str,
            exchange_name: str,
            executor: typing.Callable[[bytes], bytes],
            content_validator: typing.Optional[typing.Callable[[bytes], bool]] = None,
            queue_name: typing.Optional[str] = None,
            exchange_type: pika.exchange_type.ExchangeType = pika.exchange_type.ExchangeType.fanout,
            max_reconnection_attempts: int = 5
    ):
        """
        Initialize a new RPC server with an underlying :class:`~.basic_consumer.BasicConsumer`
        handling the receiving and sending of messages
        
        :param amqp_dsn: The Data Source Name pointing to the message broker. The message broker
            needs to support AMQPv0-9-1.
        :type amqp_dsn: str
        :param exchange_name: The name of the exchange the server will use to receive new
            messages. If the exchange does not exist the underlying
            :class:`~.basic_consumer.BasicConsumer` will create the exchange
        :type exchange_name: str
        :param executor: A method which handles the incoming message bytes and provides a
            response as bytes
        :type executor: Callable[[bytes], bytes]
        :param content_validator: A method which will validate the message content before it is
            passed to the executor
        :type content_validator: Callable[[bytes], bytes], optional
        :param queue_name: The name of the queue which will be bound to the specified exchange,
            defaults to :func:`secrets.token_urlsafe`
        :type queue_name: str, optional
        :param exchange_type: The type of exchange which will be used during the creation of the
            specified exchange. If the exchange already exists the exchange type supplied needs
            to match the one the exchange on the message broker has, defaults to
            :py:enum:`pika.exchange_type.ExchangeType.fanout`
        :type exchange_type: pika.exchange_type.ExchangeType
        """
        # = Validate AMQP Data Source Name =
        if amqp_dsn is None:
            raise ValueError('The amqp_dsn is a required parameter and may not be None')
        if len(amqp_dsn.strip()) == 0:
            raise ValueError('The amqp_dsn is a required parameter any may not be emtpy')
        if not amqp_dsn.startswith('amqp://'):
            raise ValueError('The amqp_dsn does not start with a supported URI. The supported '
                             'scheme is "amqp://"')
        # = End of AMQP Data Source Name Validation =
        # = Validate the Exchange Name =
        if exchange_name is None:
            raise ValueError('The exchange_name is a required parameter and may not be None')
        if len(exchange_name.strip()) == 0:
            raise ValueError('The exchange_name is a required parameter and may not be empty')
        # = End of Exchange Name validation =
        # = Validate the executor =
        if executor is None:
            raise ValueError('The executor is a required parameter and may not be None')
        if not inspect.isfunction(executor):
            raise TypeError('The executor is a required parameter and needs to be a method')
        # Create a signature object
        executor_signature = inspect.signature(executor)
        # Check the signature for an input as bytes
        executor_parameters = list(executor_signature.parameters.values())
        # Get the first parameter and check if the annotation is either empty or bytes
        input_param = executor_parameters[0]
        if input_param.annotation not in [inspect.Signature.empty, bytes]:
            raise TypeError('Expected the executor to accept bytes as input parameter')
        # Now check the return type
        if executor_signature.return_annotation not in [inspect.Signature.empty, bytes]:
            raise TypeError('Expected the executor to return bytes')
        # = End of executor validation =
        # = Check the content_validator if it is set =
        if content_validator is not None:
            # Check if the validator is a method
            if not inspect.isfunction(content_validator):
                raise TypeError('The content_validator needs to be a method')
            # Get the signature of the content validator
            validator_signature = inspect.signature(content_validator)
            # Get the input parameters
            validator_parameters = list(validator_signature.parameters.values())
            # Get the first input parameter
            validator_input = validator_parameters[0]
            # Check the annotation
            if validator_input.annotation not in [inspect.Signature.empty, bytes]:
                raise TypeError('The content validator needs to accept bytes as first input '
                                'argument')
            # Now check the return type
            if validator_signature.return_annotation not in [inspect.Signature.empty, bool]:
                raise TypeError('The content validator needs to return a boolean')
        # = Finished the content validator check =
        # = Check the queue name if it is set =
        if queue_name is not None:
            if len(queue_name.strip()) == 0:
                raise ValueError('When supplying a queue_name it may not be empty')
        else:
            queue_name = secrets.token_urlsafe(nbytes=32)
        # = Finished queue_name check =
        self._amqp_dsn = amqp_dsn
        self._exchange_name = exchange_name
        self._executor = executor
        self._content_validator = content_validator
        self._queue_name = queue_name
        self._exchange_type = exchange_type
        self._max_reconnection_attempts = max_reconnection_attempts
        self._current_reconnection_attempts = 0
        # Create the underlying BasicConsumer
        self._consumer = _BasicConsumer(
            amqp_dsn, exchange_name, executor, content_validator, queue_name, exchange_type
        )
        self._consumer_tread: typing.Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._error_risen = threading.Event()
        self._error: typing.Optional[Exception] = None
    
    def start_server(self):
        """Start the AMQP RPC Server and the underlying basic consumer in another thread"""
        self._consumer_tread = threading.Thread(
            target=self._start_with_reconnecting_loop,
            daemon=True
        )
        self._consumer_tread.start()
        if self._error_risen.wait():
            raise self._error
    
    def stop_server(self):
        """Stop the server and disconnect the underlying :class:`~.basicConsumer.BasicConsumer`"""
        self._stop_event.set()
        self._consumer.stop()
        self._consumer_tread.join()
    
    def _start_with_reconnecting_loop(self):
        """Start the AMQP Server with a reconnecting logic when the BasicConsumer disconnects"""
        while not self._stop_event.is_set():
            try:
                self._consumer.start()
            except Exception:  # pylint: disable=broad-except
                self._consumer.stop()
                break
            self._reconnect()
    
    def _reconnect(self):
        """Check if the server shall reconnect itself to the message broker"""
        if self._consumer.may_reconnect:
            if self._current_reconnection_attempts < self._max_reconnection_attempts:
                _logger.info('Trying to reconnect to the message broker')
                # Stop the currently running consumer
                self._consumer.stop()
                # Wait for 10 seconds
                _logger.info('Waiting five (5) seconds before opening a new connection to the '
                             'message broker')
                time.sleep(5)
                # Create a new consumer
                self._consumer = _BasicConsumer(
                    self._amqp_dsn, self._exchange_name, self._executor, self._content_validator,
                    self._queue_name, self._exchange_type
                )
                self._current_reconnection_attempts += 1
            else:
                _logger.critical('Unable to reconnect to the message broker. The maximum amount '
                                 'of reconnection attempts was reached')
                self._consumer.may_reconnect = False
                self._error_risen.set()
                self._stop_event.set()
                self._error = _MaxConnectionAttemptsReached()
