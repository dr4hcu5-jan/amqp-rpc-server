*****
Usage
*****

This chapter will contain information about how to use the server and
what things are necessary to be able to use it.


AMQP Connection Properties
==========================

For successfully starting a server you need to create a Data Source Name pointing to the message
broker. The data source name should contain credentials for connecting to the message broker. If
you do not supply credentials with the data source name the underlying :mod:`pika` package will use
``guest`` as username and password.

.. code-block:: python

    AMQP_DSN = 'amqp://<<your-username>>:<<your-password>>@<<message-broker-host>>:<<port>>/%2F'
    """A schema for a valid AMQP Data Source Name"""


Furthermore, you will need to specify a name for the exchange which will be bound to the server
for consuming incoming messages

.. code-block:: python

    EXCHANGE_NAME = '<<your-exchange-name>>'

Executor
========

Before creating a new Remote Procedure Call server you will need to write your
own executor. The executor will receive the content of the message and will execute an
action depending on the content of the message. This executor will
receive the content as :class:`bytes` encoded as `UTF-8` string, so you may
experience the most freedom in how to continue from the incoming message. The
executor will need to return your response as :class:`bytes` since the response
will be fed directly into the response message.

.. code-block:: python

    def example_executor(message_bytes: bytes) -> bytes:
        # Convert the message's content to a string
        _temporary_string = message_bytes.decode("utf-8")
        # Reverse the string
        _result_string = _temporary_string[::-1]
        # Return the string as a utf-8 bytes
        return _result_string.encode("utf-8")


Content Validator (optional)
============================

To protect your executor from invalid data you may write a content validator. The content
validator will also get it's input as :class:`bytes` from the underlying
:class:`~.basic_consumer.BasicConsumer`.
The :class:`~.basic_consumer.BasicConsumer` expects a :class:`bool` as return type

.. code-block:: python

    def example_content_validator(message_bytes: bytes) -> bool:
        """Check if the message has the content 'ping'

        :param message_bytes: The content of the incoming message
        :type message_bytes: bytes
        :returns: The result of the validation: ``True`` if the content is valid else ``False``
        :rtype: bool
        """
        # Decode the bytes from utf-8
        message_content = message_bytes.decode("utf-8")
        # Check if the message content is "ping" and return the result
        return message_content == "ping"


Full example
============

This example will demonstrate how a complete code artifact will look, if you create a new server

.. code-block:: python

    import logging
    import time

    from src.amqp_rpc_server import Server

    AMQP_DSN = "amqp://<<your-username>>:<<your-password>>@<<message-broker-host>>:<<port>>/%2F"
    """The Data Source Name pointing to the message broker which should contain credentials"""

    EXCHANGE_NAME = "example-exchange"
    """The name of the exchange which is bound by the server for incoming messages"""


    def example_executor(message_bytes: bytes) -> bytes:
        """
        An example executor which will print the message content and will just return it

        :param message_bytes: The content of the message which was received
        :type message_bytes: bytes
        :returns: The message content
        :rtype: bytes
        """
        # Print the message
        print(message_bytes.decode("utf-8"))
        # Return the message
        return message_bytes


    def example_content_validator(message_bytes: bytes) -> bool:
        """
        This content validator will check if the length of the bytes is longer than 0

        :param message_bytes: The content of the message which was received
        :type message_bytes: bytes
        :returns: True if the content is valid, false if the content is invalid
        :rtype: bool
        """
        # Check the length of the message_bytes
        if len(message_bytes) > 1:
            return True
        else:
            return False


    # Protect the script part of the application from being executed during imports
    if __name__ == "__main__":

        logging.basicConfig(
            level=logging.DEBUG,
            format='%(levelname)s - %(asctime)s %(name)s - %(funcName)s - %(lineno)s : %(message)s'
        )

        rpc_server = Server(
            AMQP_DSN,
            EXCHANGE_NAME,
            executor=example_executor,
            content_validator=example_content_validator
        )

        # Now start the server
        rpc_server.start_server()

        while True:
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                # Stop the server if CTRL-C as sent to the python interpreter
                rpc_server.stop_server()
                break
