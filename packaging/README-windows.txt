Moticam Viewer (unofficial) - for the Motic USB microscope camera (Windows)
===========================================================================

Unofficial, free software. Not affiliated with or endorsed by Motic.
Source and licence: https://github.com/goddardduncan/motic

1. Unzip the "Moticam Viewer" folder anywhere (for example Documents).
   Keep the whole folder together - the program needs the _internal folder.

2. One-time USB driver setup. Windows needs the WinUSB driver on the camera.
   If the official Motic software is installed this may already be done;
   if Moticam Viewer says "Looking for the Motic camera..." with the camera
   plugged in, do this:
     - Download Zadig from https://zadig.akeo.ie and run it.
     - Options > List All Devices.
     - Pick "Motic USB2.0" in the drop-down.
     - Make sure the target driver says WinUSB, then click Install Driver.
   (The official Motic software and Moticam Viewer can't use the camera at
   the same time - close one before opening the other.)

3. Double-click "Moticam Viewer.exe". If Windows shows "Windows protected
   your PC", click "More info" then "Run anyway". You only need to do this once.

Keys (press ; in the window for the full list):
  space    save a photo to Pictures\Motic
  a        auto exposure on/off      s / d   darker / brighter
  p        change colour pattern if colours look wrong
  7        1:1 zoom on the centre, for focusing
  Esc      quit
