@description('Region supporting Azure Functions Flex Consumption.')
param location string = 'centralindia'

@description('Existing Azure Speech resource in this resource group. Its pricing tier is unchanged.')
param speechResourceName string = 'read-aloud-speech-0b33fdb4'

@secure()
param botToken string

@secure()
param webhookSecret string

var suffix = uniqueString(resourceGroup().id, 'malayalamtts')

resource speech 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: speechResourceName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'mltts${suffix}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deployments 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'deployments'
  properties: {
    publicAccess: 'None'
  }
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource updates 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: 'telegram-updates'
}

resource tables 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource state 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tables
  name: 'TelegramBotState'
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'malayalamtts-${suffix}-plan'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

var storageConnection = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

resource bot 'Microsoft.Web/sites@2024-04-01' = {
  name: 'malayalamtts-${suffix}'
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: storageConnection
        }
        {
          name: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          value: storageConnection
        }
        {
          name: 'BOT_TOKEN'
          value: botToken
        }
        {
          name: 'TELEGRAM_WEBHOOK_SECRET'
          value: webhookSecret
        }
        {
          name: 'SPEECH_KEY'
          value: speech.listKeys().key1
        }
        {
          name: 'SPEECH_REGION'
          value: speech.location
        }
      ]
    }
    functionAppConfig: {
      runtime: {
        name: 'python'
        version: '3.12'
      }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deployments.name}'
          authentication: {
            type: 'StorageAccountConnectionString'
            storageAccountConnectionStringName: 'DEPLOYMENT_STORAGE_CONNECTION_STRING'
          }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 512
        maximumInstanceCount: 40
      }
    }
  }
  dependsOn: [
    updates
    state
  ]
}

output functionAppName string = bot.name
output storageAccountName string = storage.name
output baseUrl string = 'https://${bot.properties.defaultHostName}'
