

/* FRAMS - GPS Location Controls */

(function () {
    const button = document.getElementById("get-location");
    const status = document.getElementById("location-status");

    // Do nothing if this page has no GPS controls.
    if (!button || !status) {
        return;
    }

    function getLocation() {
        if (!navigator.geolocation) {
            status.textContent =
                "Geolocation is not supported by this browser.";
            return;
        }

        button.disabled = true;
        status.textContent = "Requesting location permission...";

        navigator.geolocation.getCurrentPosition(
            function (position) {
                const latitude = position.coords.latitude;
                const longitude = position.coords.longitude;

                status.textContent =
                    "Latitude: " + latitude +
                    " | Longitude: " + longitude;

                // Available to other scripts on this page.
                document.dispatchEvent(
                    new CustomEvent("frams:location", {
                        detail: {
                            latitude: latitude,
                            longitude: longitude
                        }
                    })
                );

                button.disabled = false;
            },
            function (error) {
                const messages = {
                    1: "Location permission denied.",
                    2: "Current location is unavailable.",
                    3: "Location request timed out."
                };

                status.textContent =
                    messages[error.code] ||
                    "Unable to retrieve location.";

                button.disabled = false;
            },
            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 0
            }
        );
    }

    button.addEventListener("click", getLocation);
})();
