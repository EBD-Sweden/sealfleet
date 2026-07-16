import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SecureInputModal } from "@/components/SecureInputModal";

// Mock fetch
global.fetch = vi.fn() as unknown as typeof fetch;

describe("SecureInputModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when closed", () => {
    render(
      <SecureInputModal
        isOpen={false}
        onClose={vi.fn()}
        fields={[{ name: "api_key", label: "API Key" }]}
        onSubmit={vi.fn()}
      />
    );
    // Dialog should not be visible
    expect(screen.queryByText("Secure Input Required")).not.toBeInTheDocument();
  });

  it("renders fields when open", () => {
    render(
      <SecureInputModal
        isOpen={true}
        onClose={vi.fn()}
        fields={[
          { name: "api_key", label: "API Key", placeholder: "sk-..." },
          { name: "secret", label: "Secret" },
        ]}
        onSubmit={vi.fn()}
      />
    );
    expect(screen.getByText("Secure Input Required")).toBeInTheDocument();
    expect(screen.getByText("API Key")).toBeInTheDocument();
    expect(screen.getByText("Secret")).toBeInTheDocument();
  });

  it("input fields are type password", () => {
    render(
      <SecureInputModal
        isOpen={true}
        onClose={vi.fn()}
        fields={[{ name: "api_key", label: "API Key" }]}
        onSubmit={vi.fn()}
      />
    );
    const inputs = screen.getAllByPlaceholderText(/enter/i);
    expect(inputs[0]).toHaveAttribute("type", "password");
  });

  it("calls onSubmit with handle ids after successful submit", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ handle_id: "test-handle-123" }),
    });
    global.fetch = mockFetch as unknown as typeof fetch;

    const onSubmit = vi.fn();
    render(
      <SecureInputModal
        isOpen={true}
        onClose={vi.fn()}
        fields={[{ name: "api_key", label: "API Key" }]}
        onSubmit={onSubmit}
      />
    );

    // Type into the password input
    const input = screen.getByPlaceholderText("Enter API Key");
    fireEvent.change(input, { target: { value: "my-secret-key" } });
    fireEvent.click(screen.getByText("Submit Securely"));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({ api_key: "test-handle-123" });
    });
  });

  it("shows warning banner about AI model", () => {
    render(
      <SecureInputModal
        isOpen={true}
        onClose={vi.fn()}
        fields={[{ name: "key", label: "Key" }]}
        onSubmit={vi.fn()}
      />
    );
    expect(
      screen.getByText(/never exposed to the AI model/i)
    ).toBeInTheDocument();
  });
});
