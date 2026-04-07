let baseImg, bangImg;
let sliderValue = 100;

function preload() {
    baseImg = loadImage('wig base.png');
    bangImg = loadImage('wig bang.png');
}

function setup() {
    const container = document.getElementById('p5-container');
    createCanvas(baseImg.width, baseImg.height, container);
}

function draw() {
    image(baseImg, 0, 0);
    
    const sliderInput = document.getElementById('bangSlider');
    sliderValue = parseFloat(sliderInput.value);
    document.getElementById('slider-value').textContent = sliderValue;
    
    // how much bang image to show
    const percentageToShow = sliderValue / 100;
    const bangHeight = bangImg.height;
    const heightToShow = bangHeight * percentageToShow;
    
    image(bangImg, 0, 0, bangImg.width, heightToShow, 0, 0, bangImg.width, heightToShow);
}
