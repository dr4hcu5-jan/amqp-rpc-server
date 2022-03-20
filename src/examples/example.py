import logging
import time

from amqp_rpc_server import Server

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
