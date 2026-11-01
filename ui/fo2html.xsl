<?xml version="1.0" encoding="UTF-8"?>
<!-- Corrections on top of zfo-editor's FO→HTML stylesheet; templates here take precedence over the imported ones. -->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:fo="http://www.w3.org/1999/XSL/Format"
                xmlns:fm="http://software602.cz/forms"
                xmlns:bl="urn:blanketa"
                exclude-result-prefixes="fo fm bl">

  <xsl:import href="../vendor/zfo-editor-src/zfo-editor-0.3.2/xslt/fo2html.xsl"/>

  <!-- Some forms name a radio button or check box only by its bind. zfo-editor reads form602radio and form602chkbx
       alone. A radio button's empty name then stops the page at the selector "#" while it loads the values,
       and a check box without a name silently gets none. -->
  <xsl:template name="radio-input">
    <xsl:variable name="name" select="(@form602radio[. != ''] | @form602bind[not(../@form602radio != '')])"/>
    <input title="{@form602tooltip}" type="radio" name="{$name}" id="{concat($name, '-', @form602on)}" value="{@form602on}">
      <xsl:apply-templates/>
    </input>
  </xsl:template>

  <xsl:template name="checkbox-input">
    <xsl:variable name="name" select="(@form602chkbx[. != ''] | @form602bind[not(../@form602chkbx != '')])"/>
    <input title="{@form602tooltip}" type="checkbox" id="{$name}" name="{$name}">
      <xsl:apply-templates/>
    </input>
  </xsl:template>

  <!-- Some forms download a code list when they open, and a form gets no network here. The field stays fillable by hand:
       as an empty list it could never be filled, and a required one would stop the form at each check. -->
  <xsl:template name="create-select">
    <xsl:param name="list-name"/>
    <xsl:param name="list-bindname"/>
    <xsl:param name="tooltip"/>
    <xsl:variable name="items" select="//fm:enum[@name=$list-name]/fm:list/fm:listitem"/>
    <xsl:choose>
      <xsl:when test="$items">
        <select title="{$tooltip}" style="width: 100%;" name="{@form602bind}">
          <xsl:call-template name="draw-list">
            <xsl:with-param name="items" select="$items"/>
          </xsl:call-template>
        </select>
      </xsl:when>
      <xsl:otherwise>
        <input type="text" title="{$tooltip}" style="width: 100%;" name="{@form602bind}"/>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template match="fo:inline[@form602ct='acm']">
    <input type="text" title="{@form602tooltip}" style="width: 100%;" name="{@form602bind}"/>
  </xsl:template>

  <!-- A button with a script names itself by its position: the script stays on the server and runs from there. -->
  <xsl:template name="button-input">
    <button type="button" title="{@form602tooltip}" zfo-app-event="{@form602btnclick}">
      <xsl:choose>
        <xsl:when test="@blanketa-button">
          <xsl:attribute name="onclick">run_form_button(this)</xsl:attribute>
          <xsl:attribute name="data-form-button"><xsl:value-of select="@blanketa-button"/></xsl:attribute>
        </xsl:when>
        <xsl:otherwise>
          <xsl:attribute name="onclick">perform_click_action(this)</xsl:attribute>
          <xsl:attribute name="value"><xsl:value-of select="@form602btnclick"/></xsl:attribute>
        </xsl:otherwise>
      </xsl:choose>
      <xsl:apply-templates/>
    </button>
  </xsl:template>

  <xsl:template match="fo:instream-foreign-object[@blanketa-button]">
    <button type="button" class="zfo-picture-button" title="{@form602tooltip}" zfo-app-event="{@form602btnclick}"
            onclick="run_form_button(this)" data-form-button="{@blanketa-button}">
      <xsl:apply-templates/>
    </button>
  </xsl:template>

  <!-- A section named only by its bind has buttons of the form's own, the page's + and - would double them. -->
  <xsl:template match="fo:block[@blanketa-section-by-bind]">
    <div name="div-rep-container">
      <div name="{@form602repe}-rep-0" class="602-repetition-block zfo-section-with-own-buttons">
        <button onclick="clone_div(this.parentNode)" type="button">+</button>
        <button onclick="del_div(this.parentNode)" type="button">-</button>
        <br/>
        <xsl:apply-templates mode="check-for-pre"/>
      </div>
    </div>
  </xsl:template>

  <!-- Barcode the form draws itself (zfo-editor drops it): an SVG from barcode.py; its text when it cannot be drawn. -->
  <xsl:template match="fm:barcode">
    <xsl:variable name="src" select="bl:barcode(string(@symbology), normalize-space(.), string(@barcode-height),
                                                string(@narrow-bar-width), string(@display-text))"/>
    <xsl:choose>
      <xsl:when test="$src != ''"><img class="zfo-barcode" src="{$src}" alt="Čárový kód {normalize-space(.)}"/></xsl:when>
      <xsl:otherwise><xsl:value-of select="normalize-space(.)"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- Embedded picture: real image type from its signature, size from the enclosing fo:instream-foreign-object. -->
  <xsl:template match="fm:internal-graphic">
    <xsl:variable name="data" select="normalize-space(text())"/>
    <xsl:variable name="type">
      <xsl:choose>
        <xsl:when test="starts-with($data, 'iVBOR')">png</xsl:when>
        <xsl:when test="starts-with($data, 'R0lG')">gif</xsl:when>
        <xsl:when test="starts-with($data, 'Qk')">bmp</xsl:when>
        <xsl:otherwise>jpeg</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:variable name="box" select="ancestor::fo:instream-foreign-object[1]"/>
    <img src="data:image/{$type};base64,{$data}" alt="">
      <xsl:attribute name="style">
        <xsl:text>display: inline-block; max-width: 100%; object-fit: contain;</xsl:text>
        <xsl:if test="$box/@width"> width: <xsl:value-of select="$box/@width"/>;</xsl:if>
        <xsl:if test="$box/@height"> height: <xsl:value-of select="$box/@height"/>;</xsl:if>
      </xsl:attribute>
    </img>
  </xsl:template>

  <!-- Some forms draw all their text and lines as one picture behind the region body, stored as a bare base64 string
       (zfo-editor puts it on <body>, where no browser loads it). It goes onto the region body as a data URI, scaled
       to its width; flow-root keeps the first block's margin inside, so the fields stay where the picture has them. -->
  <xsl:template match="fo:region-body/@background-image" mode="collect-style-attributes">
    <xsl:variable name="raw" select="normalize-space(.)"/>
    <xsl:variable name="unwrapped">
      <xsl:choose>
        <xsl:when test="starts-with($raw, 'url(')">
          <xsl:value-of select="translate(substring($raw, 5, string-length($raw) - 5), &quot;'&quot;, '')"/>
        </xsl:when>
        <xsl:otherwise><xsl:value-of select="$raw"/></xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:variable name="data" select="translate($unwrapped, '&quot;', '')"/>
    <xsl:variable name="type">
      <xsl:choose>
        <xsl:when test="starts-with($data, 'iVBOR')">png</xsl:when>
        <xsl:when test="starts-with($data, 'R0lG')">gif</xsl:when>
        <xsl:when test="starts-with($data, 'Qk')">bmp</xsl:when>
        <xsl:otherwise>jpeg</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:text>background-image: url(</xsl:text>
    <xsl:choose>
      <xsl:when test="starts-with($data, 'data:') or starts-with($data, 'http')"><xsl:value-of select="$data"/></xsl:when>
      <xsl:otherwise>data:image/<xsl:value-of select="$type"/>;base64,<xsl:value-of select="$data"/></xsl:otherwise>
    </xsl:choose>
    <xsl:text>); background-size: 100% auto; display: flow-root; </xsl:text>
  </xsl:template>

</xsl:stylesheet>
