
/* FRAMS - Camera Controls */

(function () {
    const video = document.getElementById("video");
    const canvas = document.getElementById("snapshot");
    const startButton = document.getElementById("start-camera");
    const captureButton = document.getElementById("capture");
    const stopButton = document.getElementById("stop-camera");
    const message = document.getElementById("camera-message");
    const captureStatus = document.getElementById("capture-status");

    // Safely exit if this page does not contain camera controls.
    if (!video || !startButton || !captureButton || !stopButton) {
        return;
    }

    let stream = null;

    async function startCamera() {
        if (!navigator.mediaDevices ||
            !navigator.mediaDevices.getUserMedia) {
            if (message) {
                message.textContent =
                    "Camera requires HTTPS or localhost.";
            }
            return;
        }

        try {
            stopCamera();

            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user" },
                audio: false
            });

            video.srcObject = stream;
            await video.play();

            startButton.disabled = true;
            captureButton.disabled = false;

            if (message) {
                message.textContent = "Camera is active";
            }
        } catch (error) {
            if (message) {
                message.textContent =
                    "Unable to access camera. Check browser permissions.";
            }
        }
    }

    function capturePreview() {
        if (!stream || !canvas || video.videoWidth === 0) {
            return;
        }

        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;

        const context = canvas.getContext("2d");
        context.drawImage(video, 0, 0);

        if (captureStatus) {
            captureStatus.textContent =
                "Preview captured locally. It has not been uploaded.";
        }
    }

    function stopCamera() {
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }

        video.srcObject = null;
        captureButton.disabled = true;
        startButton.disabled = false;

        if (message) {
            message.textContent = "Camera stopped";
        }
    }

    startButton.addEventListener("click", startCamera);
    captureButton.addEventListener("click", capturePreview);
    stopButton.addEventListener("click", stopCamera);

    // Release the camera when leaving the page.
    window.addEventListener("pagehide", stopCamera);
})();

