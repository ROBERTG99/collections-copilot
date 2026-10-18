// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { Modal } from "@/components/modal";

// jsdom implements `open` but not showModal()/close(); model them on the attribute.
const proto = HTMLDialogElement.prototype as Partial<HTMLDialogElement>;
const original = { showModal: proto.showModal, close: proto.close };
beforeAll(() => {
  proto.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
  proto.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  });
});
afterAll(() => {
  proto.showModal = original.showModal;
  proto.close = original.close;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open details
      </button>
      {open ? (
        <Modal
          title="Account details"
          onClose={() => {
            onClose?.();
            setOpen(false);
          }}
        >
          <p>Body text</p>
        </Modal>
      ) : null}
    </>
  );
}

function openModal() {
  const opener = screen.getByRole("button", { name: "Open details" });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

describe("Modal", () => {
  it("opens as a modal dialog labelled by its title", () => {
    render(<Harness />);
    openModal();
    const dialog = screen.getByRole("dialog", { name: "Account details" });
    expect(proto.showModal).toHaveBeenCalledTimes(1);
    expect((dialog as HTMLDialogElement).open).toBe(true);
    expect(screen.getByText("Body text")).toBeTruthy();
  });

  it("closes from the Close button and returns focus to the opener", () => {
    render(<Harness />);
    const opener = openModal();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(proto.close).toHaveBeenCalled();
    expect(document.activeElement).toBe(opener);
  });

  it("routes Escape (the cancel event) through onClose instead of the browser default", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    openModal();
    const cancel = new Event("cancel", { cancelable: true });
    fireEvent(screen.getByRole("dialog"), cancel);
    expect(cancel.defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a click on the scrim but not on a click inside", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    openModal();
    fireEvent.click(screen.getByText("Body text"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
