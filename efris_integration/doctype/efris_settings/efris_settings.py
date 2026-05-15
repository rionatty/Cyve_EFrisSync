import os

import frappe
from frappe.model.document import Document


class EFRISSettings(Document):

    def validate(self):
        if not self.enabled:
            return

        required = {
            "api_url": "EFRIS API URL",
            "tin": "TIN",
            "taxpayer_id": "Taxpayer ID",
            "device_no": "Device No",
            "app_id": "App Id",
            "efris_username": "User Name",
        }
        for field, label in required.items():
            if not self.get(field):
                frappe.throw(f"{label} is required when EFRIS Integration is enabled.")

        # Validate key file paths exist if provided
        for field, label in [
            ("public_key_path",  "EFRIS Public Key Path"),
            ("session_key_path", "Session Key Path"),
            ("private_key_path", "Private Key Path"),
        ]:
            path = self.get(field)
            if path:
                directory = os.path.dirname(path)
                if directory and not os.path.isdir(directory):
                    frappe.msgprint(
                        f"Warning: Directory for {label} does not exist: {directory}",
                        indicator="orange",
                        alert=True,
                    )

        # Mutually exclusive industry flags
        if self.alcohol_industry and self.beverage_industry:
            frappe.throw(
                "Alcohol Industry and Beverage Industry cannot both be enabled. "
                "Choose one excise calculation mode."
            )
