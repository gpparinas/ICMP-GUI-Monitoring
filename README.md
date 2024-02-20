Code Breakdown
-----------------------------------------------------------------------
Classes:
-------------------------
AutoScrollingText: Creates a textbox that automatically scrolls as new text is added.

PingApp:
-----------------------
Your main application. Manages the GUI, display of ping results, and updating those results.

GUI (Graphical User Interface):
-------------------------------------------
Uses tkinter for GUI elements (labels, textboxes, grid layout

Organizes monitored IP addresses into frames

Color-codes results for easy status check (green = good, red = down)

Uses separate consoles to display results specifically for PLDT 2TP and Globe 2TP connections.

Ping Functionality:
----------------------------------------
Multi-threaded ping implementation to concurrently check multiple IP addresses.

Stores results from threads for updating labels in the interface.
