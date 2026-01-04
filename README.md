# SF-Bus-and-Overhead-Flight-Tracker

64x32 display that shows either flights going overhead or the SF Muni schedule.

Please be warned almost all of the code has been copied from other open-source projects or created by ChatGPT. ChatGPT also did all of the work integrating the code togeather. I have linked all referenced projects and I would recommend using the original source code whenever possible.

This project has two programs that can be switched to using the buttons on the Matrix Portal M4

At Launch - Flight Tracker runs
Hold "UP" button (middle button on Matrix Portal)  - Bus Tracker runs. What works best is holding the up button until the Bus Tracker appears
Press "Down" Button (bottom button on Matrix Portal)- Flight tracker runs. Button does not need to be held
Reset the device by unplugging and replugging the device or hitting the reset button (top button on Matrix Portal)

A snake will when the device has restarted and the code is loading. 

If you did not hit anything this means the program crashed and rebooted. This happens due to memory issues with the Flight Tracker mentioned below that I am not smart enough to fix on my own! If this happens more than rarely please feel free to reach out!

Make sure to not accidentally double press the reset button twice  as that will factory reset the device.

Jen if you are reading this and you accidentally factory reset the device follow the setup in the flight portal and adafruit guides linked below but use the cody.py and settings.toml stored here. All the code is saved here so you didn't mess up!

# 1. Hardware

The hardware used is the same as smartbutnot's ["flightportal"](https://github.com/smartbutnot/flightportal/).

I used the [Adafruit starter kit](https://www.adafruit.com/product/4812) to keep purchasing simple. Hardware fit well in the 3d printed case design that is in "flightportal". Thanks to Rascal for the print assist!

Set up the hardware following [this guide](https://learn.adafruit.com/adafruit-matrixportal-m4/prep-the-matrixportal)

I have a Matrix Portal S3 on backorder as the program will auto-reset's due to memory issues. This usually seems to be on boot up or when switching programs so the effect isn't too noticable but it limited other development I wanted to do. If you don't see an update it probably means Jen convinced me not to upgrade it or I got lazy. I will post here if the upgrade is successful or fails. 

Finally I have included the images of the stickers created for Jen to decorate the case. I have also included a few alternate images that weren't printed. These were created using ChatGPT and Adobe Firefly. Adobe Firefly using Google Sora produce the best results by far although I'm sure things will be different by the time anyone reads this.

# 2. Overhead flight tracker

This project uses the flight tracking and hardware of smartbutnot's ["flightportal"](https://github.com/smartbutnot/flightportal/). 

It runs on circuit python v9 using the fork by compilebymile [here](https://github.com/compileByTheMile/flightportal)

I used the same libraries shown in compilebymile's fork. However I did have to add one additional library to get the code to work. Not sure if that was my mistake or not.

ChatGPT was used to make two changes to the code

1. ChatGPT needed to merge the flight tracker with the bus tracker and have them be switched using the matrix portal m4's buttons

ChatGPT needed to be instructed to edit as little as the code as possible when combining this with the bus tracker. I would also recommend having ChatGPT reference the code when looking to solve any issues with new code. ChatGPT would often create new problems when trying to make changes. 

ChatGPT needed to be told that each program did not need to run while the other was running and to try and conserve memory when possible. Initially efforts caused both to run at the same time and the system frequently crashed with memory issues

2. Speed (in MPH) was added to the top right corner and Altitude (in FT) was added to the bottom left corner. These were pulled but commented out in the original flightportal code. ChatGPT just had to uncomment them out and I had it mess with the strings that scrolled.


Finally there are two other projects that exist using different APIs and which have different features. They are not used in this project but will be a helpful reference if the Flight Radar 24 JSON file used goes away (as they have switched to a paid api). I would also like to incorporate airline logos if I am able to resolve my memory issues

https://github.com/AxisNimble/TheFlightWall_OSS?tab=readme-ov-file (they have a commercial version of this available to purchase)

https://github.com/ColinWaddell/FlightTracker/blob/master/README.md

https://learn.adafruit.com/matrixportal-s3-flight-proximity-tracker/overview


# 3. Bus Tracker

This code was written entirely by ChatGPT and it uses the 511 API. I'm sure there are many efficiencies that could be gained in this program but it is currently stable (or at least I hope)

Users will need to make an account and request an api key [here](https://511.org/open-data/token).

The only additional library I had to add was the adafruit_connection_manager.mpy

Most work was done setting up the API and getting the upcoming times to decrement. ChatGPT stuggled to get the api figured out and at one point said the API wasn't compatible with a matrix portal M4. Current version seems stable so I would just feed it into chatgpt as a starting point if needed.

Please note that 511 api limits you to 60 api calls per hour. The current state of the program uses 4 api calls each time it runs so I have it refresh every 4 minutes. If you add or remove stops please adjust accordingly

I am waiting for feedback on how to format the display from Jen as there are improvements to be made! If this isn't updated it means she hated it and I didn't develop further.


# 4. Final Notes

For debugging, use putty or similar, see what COM port the portal is on (device manager in windows will show you), and run a serial connection to that port at 115200. It should print out helpful messages about errors, flights it sees, etc. The adafruit connecting to serial console [guide](https://learn.adafruit.com/welcome-to-circuitpython/kattni-connecting-to-the-serial-console) was very helpful for me.

You can also paste the URLs you see in the code into a browser and check you can find flights, etc. This will be needed to check if the flight radar 24 json no longer is available. The comments in smartbutnot's flightportal are pretty active.




