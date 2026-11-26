"""GridGPU local setup, email registration, and heartbeat commands."""
import argparse
import json
from pathlib import Path
import sys

from gridgpu.registration.client import DeviceClient, RegistrationError


def print_hardware(client):
    hardware = client.inspect()
    print(f"Device ID: {client.state['deviceId']}")
    print(f"{hardware['os']} / {hardware['cpu']} / {hardware['ramTotalMiB']} MiB RAM")
    for gpu in hardware["gpus"]:
        total = gpu['vramTotalMiB']
        memory = f"{total} MiB" if total is not None else "dedicated VRAM unknown"
        print(f"Detected {gpu['name']}, {memory}, suggested tier: {gpu['suggestedTier'] or 'unclassified'}")
    if not hardware["gpus"]:
        print("No GPU detected. This installation will not claim GPU capacity.")
    print("Saved local hardware report. Tiers are advisory; hardware is self-reported.")
    return hardware


def main(argv=None):
    parser = argparse.ArgumentParser(description="GridGPU device registration")
    parser.add_argument("command", choices=["join", "setup", "inspect", "request", "status", "register", "run"])
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".gridgpu")
    parser.add_argument("--api-url", help="Your GridGPU registration_api HTTPS function URL")
    parser.add_argument("--email", help="Email for a pending registration request")
    parser.add_argument("--name", help="Donor name for the join wizard")
    parser.add_argument("--availability", help="When this GPU is available, e.g. 'nights/weekends'")
    parser.add_argument("--location", help="Donor location, e.g. 'DE'")
    parser.add_argument("--allow-local-http", action="store_true", help="Allow HTTP to a loopback development backend")
    parser.add_argument("--once", action="store_true", help="Check approval once; heartbeat only if approved")
    args = parser.parse_args(argv)
    try:
        client = DeviceClient(args.state_dir, args.api_url, args.allow_local_http)
        if args.command == "join":
            print_hardware(client)
            if not args.api_url and not client.state.get("apiUrl"):
                print("No backend URL provided. Re-run with --api-url <your registration_api URL> to join.")
                return 0
            if client.state.get("credential") or client.state.get("registrationStatus"):
                print("Resuming the existing device registration.")
                client.run(args.once)
                return 0
            if input("Join GridGPU and share this GPU? [y/N] ").strip().lower() != "y":
                print("No problem. Run 'gridgpu join' again whenever you're ready.")
                return 0
            name = args.name or input("Your name: ").strip()
            email = args.email or input("Email address: ").strip()
            availability = args.availability if args.availability is not None else input("Availability (e.g. nights/weekends, Enter to skip): ").strip()
            location = args.location if args.location is not None else input("Location (e.g. DE, Enter to skip): ").strip()
            client.request_registration(email, name, availability, location)
            print(f"A 6-digit verification code was emailed to {email}.")
            code = input("Enter the 6-digit code from your email: ").strip()
            print(json.dumps(client.register(code), indent=2))
            print("Device submitted. Waiting for the owner to approve it.")
            client.run(args.once)
        elif args.command in ("setup", "inspect"):
            print_hardware(client)
            if args.command == "inspect" or not args.api_url:
                return 0
            if client.state.get("credential") or client.state.get("registrationStatus"):
                print("Resuming the existing device registration.")
                client.run(args.once)
                return 0
            if input("Register this device? [y/N] ").strip().lower() != "y":
                return 0
            name = args.name or input("Your name: ").strip()
            email = args.email or input("Email address: ").strip()
            availability = args.availability if args.availability is not None else input("Availability (Enter to skip): ").strip()
            location = args.location if args.location is not None else input("Location (Enter to skip): ").strip()
            print(json.dumps(client.request_registration(email, name, availability, location), indent=2))
            print("Check your email for the 6-digit code. Approval happens after device submission.")
            code = input("6-digit code from your email (Enter to finish later): ").strip()
            if code:
                print(json.dumps(client.register(code), indent=2))
                client.run(args.once)
        elif args.command == "request":
            client.inspect()
            name = args.name or input("Your name: ").strip()
            email = args.email or input("Email address: ").strip()
            availability = args.availability or ""
            location = args.location or ""
            print(json.dumps(client.request_registration(email, name, availability, location), indent=2))
            print("Code emailed. Enter it using 'gridgpu register' to submit your device for approval.")
        elif args.command == "status":
            if client.state.get("registrationStatus"):
                print(json.dumps(client.poll_approval(), indent=2))
            elif client.state.get("emailStatus"):
                print(json.dumps({"status": client.state["emailStatus"],
                                  "expiresAt": client.state["tokenExpiresAt"]}, indent=2))
            else:
                print("This installation has not requested registration.")
        elif args.command == "register":
            code = "" if client.state.get("credential") or client.state.get("registrationStatus") else input("6-digit code from your email: ").strip()
            print(json.dumps(client.register(code), indent=2))
            client.run(args.once)
        elif args.command == "run":
            client.run(args.once)
        return 0
    except (RegistrationError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Stopped. Existing local device state is preserved.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
