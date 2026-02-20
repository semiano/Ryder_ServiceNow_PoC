FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY host.json /home/site/wwwroot/host.json
COPY function_app.py /home/site/wwwroot/function_app.py
COPY simulated_call_transcript.txt /home/site/wwwroot/simulated_call_transcript.txt
COPY src /home/site/wwwroot/src
COPY system_documentation /home/site/wwwroot/system_documentation
