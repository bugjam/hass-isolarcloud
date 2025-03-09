# iSolarCloud integration for HomeAssistant

This integration retrieves data from the [iSolarCloud API](https://developer-api.isolarcloud.com/) provided by Sungrow.

## Status
This integration is quite new and might still have bugs. It runs on my HomeAssistant installation but it is not a "proven" product yet.

The iSolarCloud API itself is also new and does not seem very mature. Their OAuth2 implementation required some workarounds, I will document more details on them soon.

## Configuration

1. Create an account in the [Sungrow Developer Portal](https://developer-api.isolarcloud.com/)
2. Create a new app
3. Wait for Sungrow to approve your app
4. When the app is approved, you can find the needed configuration details in the devloper portal

## Installation

The integration can be installed with [HACS](https://hacs.xyz):

1. Add `https://github.com/bugjam/hass-isolarcloud` as a user defined repository in HACS
2. Install *iSolarCloud* from the HACS store
3. Restart HomeAssistant
4. Install *iSolarCloud* in HomeAssistant: Settings -> Integrations -> Add integration
5. Select your iSolarCloud server
6. The integrations uses HomeAssistant's [Application Credentials](https://www.home-assistant.io/integrations/application_credentials/) integration to manage the OAuth2 configuration. You will be prompted to enter *Client Id* and *Client Secret*
   * *Client Id* must contain two values from the Sungrow developer portal: *ApplicationId* and *Appkey*, separated by a `@`. Example: `499@586FEE69FC005B17361AB992FC5B1CEA`
   * *Client Secret* is the *Secret Key*
7. HomeAssistant will show a button to navigate to iSolarCloud
8. Log in and select the plant you want to fetch data for
9. You will be redirected back to [My Home Assistant](https://www.home-assistant.io/integrations/my/)