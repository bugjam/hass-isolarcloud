<?php

// Configuration
$authServerUrl = "https://web3.isolarcloud.eu/#/authorized-app";
$redirectUri = "https://bounce.e-dreams.dk/isolarcloud/";
$clientCallbackUri = "https://my.home-assistant.io/redirect/oauth";

// Start the OAuth flow
if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['state']) && !isset($_GET['code'])) {
    $state = $_GET['state'];

    // Store state in a secure, HTTP-only cookie
    setcookie("oauth_state", $state, [
        'expires' => time() + 300, // 5 minutes
        'httponly' => true,
        'secure' => true,
        'samesite' => 'Lax'
    ]);

    // Redirect to the actual authorization endpoint
    if(isset($_GET['cloudUrl'])){
        $authServerUrl = $_GET['cloudUrl'];
    }
    $authUrl = $authServerUrl . '?' . http_build_query([
        'cloudId' => $_GET['cloudId'],
        'applicationId' => $_GET['applicationId'],
        'redirectUrl' => $redirectUri
    ]);
    
    header("Location: $authUrl");
    exit;
}

// Handle the OAuth callback
if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['code'])) {
    // Retrieve state from the cookie
    $state = $_COOKIE['oauth_state'] ?? null;
    if (!$state) {
        http_response_code(400);
        echo "<b>Your configuration needs to be updated</b><br>";
        echo "Thank you for using the iSolarCloud integration. <br>";
        echo "Please reconfigure your Redirect URI in the ";
        echo "<a href=\"https://developer-api.isolarcloud.com/\" target=\"_new\">Sungrow Developer Portal</a> to:<br>";
        echo "<pre>$clientCallbackUri</pre>";
        echo "and then try the authorisation flow again.<br><br>";
        echo "(This message occurs after you have updated the iSolarCloud integration to v0.5.0 or newer which uses a new redirect URI.)";
        exit;
    }

    // Clear the state cookie after use
    setcookie("oauth_state", "", time() - 3600);

    // Redirect to the client application with the correct state
    $clientRedirectUrl = $clientCallbackUri . '?' . http_build_query([
        'code' => $_GET['code'],
        'state' => $state
    ]);

    header("Location: $clientRedirectUrl");
    exit;
}

// If accessed directly
http_response_code(400);
echo "Invalid request";
exit;
?>
