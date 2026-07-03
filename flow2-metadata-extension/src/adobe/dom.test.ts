import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_PREFERENCES } from "../storage";
import { applyAdobeAiDeclarations, applyPortfolioMetadata, applyUploadMetadata, assetImages, saveAdobeForm, setNativeValue } from "./dom";

function makeSaveConfirmOnClick(button: HTMLButtonElement): void {
  button.addEventListener("click", () => { button.disabled = true; });
}

describe("Adobe DOM compatibility", () => {
  beforeEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("discovers upload and portfolio fixtures independently", () => {
    document.body.innerHTML = '<img class="upload-tile__thumbnail upload-tile__thumbnail--portrait"><img class="content-thumbnail__img">';
    expect(assetImages("upload")).toHaveLength(1);
    expect(assetImages("portfolio")).toHaveLength(1);
  });

  it("updates controlled inputs with input and change events", () => {
    const input = document.createElement("input");
    const inputEvent = vi.fn();
    const changeEvent = vi.fn();
    input.addEventListener("input", inputEvent);
    input.addEventListener("change", changeEvent);
    setNativeValue(input, "new value");
    expect(input.value).toBe("new value");
    expect(inputEvent).toHaveBeenCalledOnce();
    expect(changeEvent).toHaveBeenCalledOnce();
  });

  it("fills metadata, applies both AI declarations, then saves exactly once", async () => {
    document.body.innerHTML = `
      <textarea id="content-title-ui-textarea"></textarea>
      <textarea id="content-keywords-ui-textarea"></textarea>
      <div role="option" data-key="10001"></div>
      <label for="ai-made">Created using generative AI tools</label><input id="ai-made" type="checkbox" />
      <div id="conditional"></div>
      <button type="button">Save work</button>`;
    const ai = document.querySelector<HTMLInputElement>("#ai-made")!;
    ai.addEventListener("change", () => {
      if (ai.checked) document.querySelector("#conditional")!.innerHTML = '<label><input id="fictional" type="checkbox" /> People and Property are fictional</label>';
    });
    const save = document.querySelector<HTMLButtonElement>("button")!;
    const saveClick = vi.spyOn(save, "click");
    const onSaving = vi.fn();
    save.addEventListener("click", () => {
      expect(onSaving).toHaveBeenCalledOnce();
      expect(ai.checked).toBe(true);
      expect(document.querySelector<HTMLInputElement>("#fictional")?.checked).toBe(true);
      save.disabled = true;
    });

    await applyUploadMetadata({ title: "Wild bird", keywords: "bird, wildlife", category: "10001" }, DEFAULT_PREFERENCES, onSaving);
    expect((document.querySelector("#content-title-ui-textarea") as HTMLTextAreaElement).value).toBe("Wild bird");
    expect(saveClick).toHaveBeenCalledOnce();
  });

  it("supports Adobe's recognizable people/property No-button variant", async () => {
    document.body.innerHTML = `
      <label><input id="ai-made" type="checkbox" /> Created using generative AI tools</label>
      <section>Recognizable people or property?<button>Yes</button><button id="no-option">No</button></section>`;
    const no = document.querySelector<HTMLButtonElement>("#no-option")!;
    const noClick = vi.spyOn(no, "click");
    await applyAdobeAiDeclarations(DEFAULT_PREFERENCES);
    expect(document.querySelector<HTMLInputElement>("#ai-made")?.checked).toBe(true);
    expect(noClick).toHaveBeenCalledOnce();
  });

  it("can disable both Adobe declarations", async () => {
    document.body.innerHTML = `
      <label for="ai-made">Created using generative AI tools</label><input id="ai-made" type="checkbox" checked />
      <label><input id="fictional" type="checkbox" checked /> People and Property are fictional</label>`;
    await applyAdobeAiDeclarations({ ...DEFAULT_PREFERENCES, markGenerativeAi: false, confirmFictionalPeopleProperty: false });
    expect(document.querySelector<HTMLInputElement>("#ai-made")?.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>("#fictional")?.checked).toBe(true);
  });

  it("requires Save work to expose a verifiable saving state", async () => {
    document.body.innerHTML = '<button>Save work</button>';
    const save = document.querySelector<HTMLButtonElement>("button")!;
    makeSaveConfirmOnClick(save);
    await expect(saveAdobeForm()).resolves.toBeUndefined();
    expect(save.disabled).toBe(true);
  });

  it("fails instead of advancing when Save work never confirms", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<button>Save work</button>';
    const save = document.querySelector<HTMLButtonElement>("button")!;
    const saveClick = vi.spyOn(save, "click");
    const assertion = expect(saveAdobeForm()).rejects.toThrow('Adobe did not confirm "Save work" completion.');
    await vi.advanceTimersByTimeAsync(10_100);
    await assertion;
    expect(saveClick).toHaveBeenCalledOnce();
  });

  it("fills the legacy portfolio editors and verifies saving", async () => {
    document.body.innerHTML = `
      <button class="editable__pencil"></button>
      <input class="input--full" />
      <button class="button__text text-up"></button>
      <button class="button button--floating editable__pencil margin-left-small"></button>
      <input data-t="content-keyword" /><input data-t="content-keyword" />
      <button class="button button--dialog"></button>
      <button type="button">Save work</button>`;
    const save = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Save work")!;
    makeSaveConfirmOnClick(save);
    const saveClick = vi.spyOn(save, "click");
    await applyPortfolioMetadata({ title: "City skyline", keywords: "city, skyline", category: "" });
    expect((document.querySelector(".input--full") as HTMLInputElement).value).toBe("City skyline");
    expect(Array.from(document.querySelectorAll<HTMLInputElement>('[data-t="content-keyword"]')).map((input) => input.value))
      .toEqual(["city", "skyline"]);
    expect(saveClick).toHaveBeenCalledOnce();
  });
});
