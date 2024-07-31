import threading
import time
import tkinter as tk
from tkinter import Text
from ping3 import ping
import requests
import validators
class AutoScrollingText:
    def __init__(self, master, **kwargs):
        self.text = Text(master, **kwargs)
        self.text.config(yscrollcommand=False)
        
class PingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Iloilo Nearsol | Ping Monitor")

        # Set the window resolution
        self.geometry("1280x720")

        # Add an attribute to store the current full-screen state
        self.is_fullscreen = False

        # Bind F11 key to toggle full-screen mode
        self.bind("<F11>", self.toggle_fullscreen)
        
        self.web_services = {
        "Amazon": "https://aws.amazon.com",
        "Microsoft":"https://www.office.com",
        "Google": "https://google.com"
        }
          
        self.web_service_labels = {}  # Initialize here
        
        for idx, name in enumerate(self.web_services):
            row = idx // 3 + 5  # Place web service results below ping results
            col = idx % 3
            frame = tk.Frame(self, bg="black")
            frame.grid(row=row * 2, column=col * 2, columnspan=2, padx=14, pady=26, sticky='nsew')
            self.grid_rowconfigure(row * 2, weight=1)
            self.grid_columnconfigure(col * 2, weight=1)  

            label = tk.Label(frame, text=name, font=("Arial", 18), bg="black", fg="white")
            label.pack(anchor='w')
            result_label = tk.Label(frame, text="", font=("Arial", 17), bg="black", fg="white", wraplength="400",justify='center')
            result_label.pack(fill='both', expand=True)
            self.web_service_labels[name] = result_label
    
        self.ip_addresses = {
           "Google DNS": "8.8.8.8",
            "wah.gsipartners.com": "199.241.233.147",
            "evdi.sdsacloud.com": "157.197.66.126",
            "vpn-da2.omnicare365.com": "40.142.101.204",
            "PLDT Radial Gateway": "58.69.0.89",
            "PLDT Medrisk Gateway": "115.146.189.62"
        }

        self.configure(bg="black")

        self.frames = {}
        self.labels = {}
        for idx, name in enumerate(self.ip_addresses):
            row = idx // 3
            col = idx % 3
            frame = tk.Frame(self, bg="black")
            frame.grid(row=row * 2, column=col * 2, columnspan=2, padx=14, pady=26, sticky='nsew')
            self.grid_rowconfigure(row * 2, weight=1)
            self.grid_columnconfigure(col * 2, weight=1)

            self.frames[name] = frame

            label = tk.Label(frame, text=name, font=("Arial", 18), bg="black", fg="white")
            label.pack(anchor='w')
            result_label = tk.Label(frame, text="", font=("Arial", 17), bg="black", fg="white", wraplength="400",justify='center')
            result_label.pack(fill='both', expand=True)
            self.labels[name] = result_label

        for i in range(5):
            self.grid_rowconfigure(i, weight=1)
        for i in range(6):
            self.grid_columnconfigure(i, weight=1)

        self.console = AutoScrollingText(self, bg="black", fg="white", height=16, font=("Arial", 12))
        self.console.text.grid(row=4, column=0, columnspan=3, padx=10, pady=16, sticky="nsew")

        self.console_globe = AutoScrollingText(self, bg="black", fg="white", height=16, font=("Arial", 12))
        self.console_globe.text.grid(row=4, column=3, columnspan=3, padx=10, pady=16, sticky="nsew")

        self.update_ping_results()

    def toggle_fullscreen(self, event=None):
        self.is_fullscreen = not self.is_fullscreen
        self.attributes("-fullscreen", self.is_fullscreen)
        return "break"

    def check_web_service(self, name, url, timeout=5):
            print(f"Checking URL: {url}")
            if not validators.url(url):
                print(f"URL Failed validation: {url}")
                return "Invalid URL: URL failed validation"
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()  # Raise an exception for HTTP errors

                delay = response.elapsed.total_seconds()
                result = f"OK ({delay:.2f}s)" if delay < 1 else f"OK (but slow: {delay:.2f}s)"
            except requests.exceptions.MissingSchema:
                    result = "Invalid URL: Missing scheme (http:// or https://)"
            except requests.exceptions.ConnectionError:
                    result = "Connection Error"
            except requests.exceptions.Timeout:
                    result = "Timeout"
            except requests.exceptions.RequestException as e:
                    result = f"HTTP Error: {e}"
            return result
        
        
    def update_ping_results(self):
        def ping_ip(name, ip_address, timeout=2):
            try:
                delay = ping(ip_address, timeout=timeout) * 1000
                if delay is not None:
                    delay_str = "0.{:03d}".format(int(delay))
                    if delay >= 200.0:
                        result = f"Responded in {delay_str} ms"
                        color = "yellow"
                    else:
                        result = f"Responded in {delay_str} ms"
                        color = "green"
                else:
                    result = f"Down or not responding within the timeout"
                    color = "red"
            except Exception as e:
                result = f"Error pinging {name} ({ip_address}): {e}"
                color = "red"
            return result, color

        web_service_threads = []
        
        for name, url in self.web_services.items():
            t = threading.Thread(name=name, target=self.check_web_service, args=(name, url))
            t.start()
            web_service_threads.append(t)

        for t in web_service_threads:
            t.join()
            result = t.result  # Fetch the result stored in the Thread object
            if "OK" in result:
                self.web_service_labels[t.name].config(text=result, fg="green")
            else:
                self.web_service_labels[t.name].config(text=result, fg="red")
                
        threads = []

        for name, ip in self.ip_addresses.items():
            t = threading.Thread(name=name, target=ping_ip, args=(name, ip))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()
            result, color = t.result  # Unpack the result and color
            self.labels[t.name].config(text=result, fg=color)  # Use the color for text
            if t.name in ["wah.gsipartners.com", "evdi.sdsacloud.com"]:
                console_to_use = self.console if t.name == "wah.gsipartners.com" else self.console_globe
                console_to_use.text.insert(tk.END, f"{t.name}: {result}\n")
                console_to_use.text.see(tk.END)

                    
        self.after(1000, self.update_ping_results)

if __name__ == "__main__":
    # Set the result attribute for the Thread class to store results
    threading.Thread.result = None
    def run_with_result(self, *args, **kwargs):
        self.result = self._target(*self._args, **self._kwargs)
    threading.Thread.run = run_with_result
    app = PingApp()
    app.mainloop()